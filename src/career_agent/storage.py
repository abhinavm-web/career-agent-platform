"""Local SQLite persistence and safe application state transitions."""
from __future__ import annotations

import sqlite3
import re
from pathlib import Path

from .models import Job


class DescriptionConflict(ValueError):
    """A description was updated after the editor loaded it."""


class Store:
    def __init__(self, path: str | Path = ".local/career_agent.sqlite3") -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                url TEXT NOT NULL,
                description TEXT NOT NULL,
                source TEXT NOT NULL,
                remote INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications(
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                stage TEXT NOT NULL CHECK(stage IN ('ready_for_review','submitted')),
                receipt TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS packet_reviews(
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                packet_sha256 TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(
                    decision IN ('approved_for_manual_submission','rejected_by_reviewer')
                ),
                decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_packet_reviews_job
                ON packet_reviews(job_id, review_id DESC);
            CREATE TABLE IF NOT EXISTS job_description_revisions(
                job_id TEXT NOT NULL REFERENCES jobs(job_id), revision INTEGER NOT NULL,
                previous_description TEXT NOT NULL, description TEXT NOT NULL,
                source_note TEXT NOT NULL, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(job_id, revision)
            );
        """)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()

    def add_jobs(self, jobs: list[Job]) -> int:
        inserted = 0
        with self.connection:
            for job in jobs:
                result = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO jobs
                    (job_id,company,title,location,url,description,source,remote)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (job.id, job.company, job.title, job.location,
                     job.url, job.description, job.source, int(job.remote)),
                )
                inserted += result.rowcount
        return inserted

    def list_jobs(self, limit: int = 100) -> list[Job]:
        rows = self.connection.execute(
            "SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Job(
                company=row["company"], title=row["title"],
                location=row["location"], url=row["url"],
                description=row["description"], source=row["source"],
                remote=bool(row["remote"]),
            )
            for row in rows
        ]

    def job_page(self, limit: int = 500, before: int | None = None) -> tuple[list[Job], int | None]:
        """Read a bounded, insertion-stable page ordered from newest to oldest."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Page size must be between 1 and 500')
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError('Invalid opportunity cursor')
        if before is None:
            rows = self.connection.execute(
                'SELECT rowid AS page_rowid, * FROM jobs ORDER BY rowid DESC LIMIT ?', (limit + 1,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                'SELECT rowid AS page_rowid, * FROM jobs WHERE rowid < ? ORDER BY rowid DESC LIMIT ?',
                (before, limit + 1)
            ).fetchall()
        next_before = rows[limit - 1]['page_rowid'] if len(rows) > limit else None
        return [Job(company=row['company'], title=row['title'], location=row['location'],
                    url=row['url'], description=row['description'], source=row['source'],
                    remote=bool(row['remote'])) for row in rows[:limit]], next_before

    def get_job(self, job_id: str) -> Job:
        row = self.connection.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown job ID: {job_id}")
        return Job(
            company=row["company"], title=row["title"],
            location=row["location"], url=row["url"],
            description=row["description"], source=row["source"],
            remote=bool(row["remote"]),
        )

    def description_revision(self, job_id: str) -> int:
        row = self.connection.execute('SELECT MAX(revision) FROM job_description_revisions WHERE job_id=?', (job_id,)).fetchone()
        return row[0] or 0

    def description_latest(self, job_id: str) -> dict | None:
        row = self.connection.execute('SELECT revision,source_note,changed_at FROM job_description_revisions WHERE job_id=? ORDER BY revision DESC LIMIT 1', (job_id,)).fetchone()
        return dict(row) if row else None

    def description_history(self, job_id: str) -> list[dict]:
        self.get_job(job_id)
        return [dict(row) for row in self.connection.execute(
            'SELECT revision,previous_description,description,source_note,changed_at FROM job_description_revisions WHERE job_id=? ORDER BY revision DESC LIMIT 50',
            (job_id,))]

    def correct_description(self, job_id: str, description: str, expected_revision: int, source_note: str) -> dict:
        if not isinstance(description, str) or not description.strip() or len(description) > 50000 or '\x00' in description:
            raise ValueError('Description must contain 1–50,000 characters without NUL')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected description revision must be a nonnegative integer')
        if not isinstance(source_note, str) or len(source_note) > 500 or '\x00' in source_note:
            raise ValueError('Source note must be at most 500 characters without NUL')
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            job = self.get_job(job_id)
            if self.stage_of(job_id) == 'submitted':
                raise ValueError('Submitted applications are locked; their posting cannot be edited')
            previous_revision = self.description_revision(job_id)
            if expected_revision != previous_revision:
                raise DescriptionConflict('Job description changed in another session; reload before editing')
            if description == job.description:
                return {'revision': previous_revision, 'changed': False}
            revision = previous_revision + 1
            self.connection.execute('UPDATE jobs SET description=? WHERE job_id=?', (description, job_id))
            self.connection.execute('INSERT INTO job_description_revisions(job_id,revision,previous_description,description,source_note) VALUES(?,?,?,?,?)',
                (job_id, revision, job.description, description, source_note.strip()))
        return {'revision': revision, 'changed': True}

    def stage_of(self, job_id: str) -> str:
        row = self.connection.execute(
            "SELECT stage FROM applications WHERE job_id=?", (job_id,)
        ).fetchone()
        return row["stage"] if row else "discovered"

    def stage(self, job_id: str) -> None:
        self.get_job(job_id)
        if self.stage_of(job_id) == "submitted":
            raise ValueError("Submitted jobs cannot be restaged")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO applications(job_id,stage)
                VALUES(?,'ready_for_review')
                ON CONFLICT(job_id) DO UPDATE SET
                stage='ready_for_review',updated_at=CURRENT_TIMESTAMP
                """, (job_id,)
            )

    def confirm(self, job_id: str, receipt: str, attested: bool = False) -> None:
        if not attested:
            raise ValueError("Explicit user attestation of submission is required")
        if len(receipt.strip()) < 4:
            raise ValueError("Employer confirmation reference must be provided")
        if self.stage_of(job_id) != "ready_for_review":
            raise ValueError("Stage this job before recording a submission")
        with self.connection:
            self.connection.execute(
                """
                UPDATE applications SET
                stage='submitted', receipt=?, updated_at=CURRENT_TIMESTAMP
                WHERE job_id=?
                """, (receipt.strip(), job_id)
            )


    def record_review(self, job_id: str, packet_sha256: str, approved: bool, *, expected_description_revision: int | None = None) -> dict:
        """Append-only record of explicit human review. Does not send anything."""
        if type(approved) is not bool:
            raise ValueError("Review approval must be a boolean")
        if not isinstance(packet_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", packet_sha256):
            raise ValueError("Invalid packet SHA-256 fingerprint")
        decision = "approved_for_manual_submission" if approved else "rejected_by_reviewer"
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            self.get_job(job_id)
            if self.stage_of(job_id) == "submitted":
                raise ValueError("A submitted application cannot receive a new review decision")
            if expected_description_revision is not None and self.description_revision(job_id) != expected_description_revision:
                raise DescriptionConflict('Job description changed; regenerate and review the draft')
            cursor = self.connection.execute(
                """
                INSERT INTO packet_reviews(job_id,packet_sha256,decision)
                VALUES (?,?,?)
                """,
                (job_id, packet_sha256, decision),
            )
        return {
            "id": cursor.lastrowid,
            "job_id": job_id,
            "packet_sha256": packet_sha256,
            "decision": decision,
        }

    def latest_review(self, job_id: str, packet_sha256: str | None = None) -> dict | None:
        """Return last decision for a job's current packet, never a stale packet."""
        self.get_job(job_id)
        sql = """
            SELECT review_id,job_id,packet_sha256,decision,decided_at
            FROM packet_reviews WHERE job_id=?
        """
        params: tuple = (job_id,)
        if packet_sha256 is not None:
            sql += " AND packet_sha256=?"
            params = (job_id, packet_sha256)
        row = self.connection.execute(
            sql + " ORDER BY review_id DESC LIMIT 1", params
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def review_history(self, limit: int = 30) -> list[dict]:
        """List review actions without disclosing private packets or receipts."""
        rows = self.connection.execute(
            """
            SELECT r.review_id,r.job_id,r.packet_sha256,r.decision,r.decided_at,
                   j.company,j.title
            FROM packet_reviews AS r JOIN jobs AS j ON r.job_id=j.job_id
            ORDER BY r.review_id DESC LIMIT ?
            """,
            (max(0, min(limit, 100)),),
        ).fetchall()
        return [dict(row) for row in rows]
