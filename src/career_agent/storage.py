"""Local SQLite persistence and safe application state transitions."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Job


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
