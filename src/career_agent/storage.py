"""Local SQLite persistence and safe application state transitions."""
from __future__ import annotations

import hashlib
import sqlite3
import re
from pathlib import Path

from .models import Job, merge_discovery_rows


def _description_digest(description: str) -> str | None:
    # Version the digest because older observations discarded case and layout;
    # those cannot establish an exact match without seeing the source again.
    return 'v2:' + hashlib.sha256(description.encode('utf-8')).hexdigest() if description.strip() else None


def _candidate_text_valid(job: Job) -> bool:
    values = (job.description, job.title, job.company)
    if (not job.description.strip() or len(job.description) > 50000 or len(job.title) > 200 or
            len(job.company) > 300 or any('\x00' in value for value in values)):
        return False
    try:
        for value in values:
            value.encode('utf-8')
    except UnicodeError:
        return False
    return True


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
            CREATE TABLE IF NOT EXISTS job_discovery_observations(
                job_id TEXT NOT NULL REFERENCES jobs(job_id), source TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                observation_count INTEGER NOT NULL,
                PRIMARY KEY(job_id, source)
            );
            CREATE TABLE IF NOT EXISTS job_discovery_descriptions(
                job_id TEXT NOT NULL REFERENCES jobs(job_id), source TEXT NOT NULL,
                description_sha256 TEXT, observed_at TEXT NOT NULL,
                PRIMARY KEY(job_id, source)
            );
            CREATE TABLE IF NOT EXISTS job_discovery_posting_dates(
                job_id TEXT NOT NULL REFERENCES jobs(job_id), source TEXT NOT NULL,
                reported_date TEXT, status TEXT NOT NULL, observed_at TEXT NOT NULL,
                PRIMARY KEY(job_id, source)
            );
            CREATE TABLE IF NOT EXISTS job_description_candidates(
                job_id TEXT NOT NULL REFERENCES jobs(job_id), source TEXT NOT NULL,
                description TEXT NOT NULL, title TEXT NOT NULL, company TEXT NOT NULL,
                observed_at TEXT NOT NULL, PRIMARY KEY(job_id, source)
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

    def _record_discovery(self, jobs: list[Job], *, stage_descriptions: bool = False, stage_changed_descriptions: bool = False) -> int:
        """Record returned rows within the caller's transaction, once per job/source."""
        inserted = 0
        for job in merge_discovery_rows(jobs):
            key = (job.id, job.source)
            result = self.connection.execute(
                'INSERT OR IGNORE INTO jobs(job_id,company,title,location,url,description,source,remote) VALUES(?,?,?,?,?,?,?,?)',
                (job.id, job.company, job.title, job.location, job.url, job.description, job.source, int(job.remote)))
            inserted += result.rowcount
            if (stage_descriptions and not result.rowcount and job.source in {'indeed', 'linkedin', 'zip_recruiter'}
                    and _candidate_text_valid(job)):
                saved = self.connection.execute('SELECT url,description FROM jobs WHERE job_id=?', (job.id,)).fetchone()
                if (saved and saved['url'] == job.url and self.stage_of(job.id) != 'submitted'
                        and (not saved['description'].strip() or
                             (stage_changed_descriptions and saved['description'] != job.description))):
                    self.connection.execute('''INSERT INTO job_description_candidates(job_id,source,description,title,company,observed_at)
                        VALUES(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                        ON CONFLICT(job_id,source) DO UPDATE SET description=excluded.description,
                        title=excluded.title,company=excluded.company,observed_at=excluded.observed_at''',
                        (job.id, job.source, job.description, job.title, job.company))
            self.connection.execute('''
                INSERT INTO job_discovery_observations(job_id,source,first_seen,last_seen,observation_count)
                VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'),1)
                ON CONFLICT(job_id,source) DO UPDATE SET
                last_seen=excluded.last_seen, observation_count=observation_count+1
            ''', key)
            self.connection.execute('''
                INSERT INTO job_discovery_descriptions(job_id,source,description_sha256,observed_at)
                VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(job_id,source) DO UPDATE SET
                description_sha256=excluded.description_sha256, observed_at=excluded.observed_at
            ''', (job.id, job.source, _description_digest(job.description)))
            self.connection.execute('''
                INSERT INTO job_discovery_posting_dates(job_id,source,reported_date,status,observed_at)
                VALUES(?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(job_id,source) DO UPDATE SET reported_date=excluded.reported_date,
                    status=excluded.status, observed_at=excluded.observed_at
            ''', (job.id, job.source, job.reported_posted_date, job.posted_date_status))
        return inserted

    def add_discovered_jobs(self, jobs: list[Job], *, stage_descriptions: bool = False, stage_changed_descriptions: bool = False) -> int:
        """Atomically import jobs and record when a source returned each URL."""
        with self.connection:
            return self._record_discovery(jobs, stage_descriptions=stage_descriptions, stage_changed_descriptions=stage_changed_descriptions)

    def description_candidates(self, job_id: str) -> list[dict]:
        rows = self.connection.execute('''SELECT source,description,title,company,observed_at
            FROM job_description_candidates WHERE job_id=? ORDER BY observed_at DESC,source''', (job_id,))
        return [dict(row) for row in rows]

    def discovery_observations(self, job_ids: list[str]) -> dict[str, list[dict]]:
        if not job_ids:
            return {}
        placeholders = ','.join('?' for _ in job_ids)
        rows = self.connection.execute(
            f'''SELECT o.job_id,o.source,o.first_seen,o.last_seen,o.observation_count,
                d.description_sha256,d.observed_at,j.description,
                p.reported_date,p.status AS posted_date_status,p.observed_at AS posted_date_observed_at,
                c.observed_at AS candidate_observed_at, c.description <> j.description AS candidate_differs
                FROM job_discovery_observations o JOIN jobs j ON j.job_id=o.job_id
                LEFT JOIN job_discovery_descriptions d ON d.job_id=o.job_id AND d.source=o.source
                LEFT JOIN job_discovery_posting_dates p ON p.job_id=o.job_id AND p.source=o.source
                LEFT JOIN job_description_candidates c ON c.job_id=o.job_id AND c.source=o.source
                WHERE o.job_id IN ({placeholders}) ORDER BY o.last_seen DESC,o.source''', job_ids)
        result: dict[str, list[dict]] = {}
        for row in rows:
            item = {k: row[k] for k in ('source','first_seen','last_seen','observation_count')}
            digest = row['description_sha256']
            item['description_comparison'] = ('unknown' if digest is None or not digest.startswith('v2:') else
                'matches' if digest == _description_digest(row['description']) else 'differs')
            item['description_observed_at'] = row['observed_at']
            item['candidate_observed_at'] = row['candidate_observed_at']
            item['candidate_differs'] = bool(row['candidate_differs']) if row['candidate_observed_at'] else False
            item['reported_posted_date'] = row['reported_date']
            item['posted_date_status'] = row['posted_date_status'] or 'unknown'
            item['posted_date_observed_at'] = row['posted_date_observed_at']
            result.setdefault(row['job_id'], []).append(item)
        return result

    def possible_duplicates(self, job_id: str) -> dict:
        """Find possible same-title postings without changing either record."""
        job = self.get_job(job_id)
        normalize = lambda value: ' '.join(value.casefold().split())
        location = normalize(job.location)
        if not location:
            return {'jobs': [], 'scan_limited': False, 'results_truncated': False,
                    'reason': 'Location is missing; compare other postings manually.'}
        matches = []
        scan_limited = False
        results_truncated = False
        rows = self.connection.execute('''SELECT job_id,company,title,location,url,source FROM jobs
            WHERE job_id<>? ORDER BY rowid DESC LIMIT 10001''', (job.id,))
        for index, row in enumerate(rows):
            if index == 10000:
                scan_limited = True
                break
            if (normalize(row['company']) != normalize(job.company) or
                normalize(row['title']) != normalize(job.title) or
                normalize(row['location']) != location):
                continue
            if len(matches) == 10:
                results_truncated = True
                break
            item = dict(row)
            item['submission_recorded'] = self.stage_of(row['job_id']) == 'submitted'
            matches.append(item)
        return {'jobs': matches, 'scan_limited': scan_limited, 'results_truncated': results_truncated,
                'reason': 'Exact company, title and location after case and whitespace normalization only. Matching details can still represent different requisitions.'}

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

    def workspace_revision(self) -> int:
        """Read the revision without initializing or committing within a transaction."""
        exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_workspace_versions'").fetchone()
        if not exists:
            return 0
        row = self.connection.execute('SELECT MAX(revision) FROM candidate_workspace_versions').fetchone()
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


    def record_review(self, job_id: str, packet_sha256: str, approved: bool, *, expected_description_revision: int | None = None,
                      expected_workspace_revision: int | None = None) -> dict:
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
            if expected_workspace_revision is not None and self.workspace_revision() != expected_workspace_revision:
                raise DescriptionConflict('Career profile changed; regenerate and review the draft')
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
