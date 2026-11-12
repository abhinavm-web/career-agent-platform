"""Local-only application CRM: pipeline, recruiter contacts, and follow-up tasks.

All records stay in the user's existing SQLite database. This module only
records candidate-entered facts; it never sends mail or job applications.
Application submission remains the independent Store.confirm() boundary.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .storage import Store

PRE_APPLICATION = {
    "discovered": ("saved", "shortlisted", "archived"),
    "saved": ("shortlisted", "archived"),
    "shortlisted": ("saved", "archived"),
    "archived": ("saved", "shortlisted"),
}
POST_APPLICATION = {
    "applied": ("screening", "assessment", "interview", "offer", "rejected", "withdrawn"),
    "screening": ("assessment", "interview", "offer", "rejected", "withdrawn"),
    "assessment": ("interview", "offer", "rejected", "withdrawn"),
    "interview": ("assessment", "offer", "rejected", "withdrawn"),
    "offer": ("accepted", "rejected", "withdrawn"),
    "accepted": (),
    "rejected": (),
    "withdrawn": (),
}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TERMINAL_STAGES = {'accepted', 'rejected', 'withdrawn'}


class StageConflict(ValueError):
    """The pipeline changed since the correction was displayed."""


def _text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum or (required and not result):
        raise ValueError(f"{field} must be {'nonempty and ' if required else ''}at most {maximum} characters")
    return result


def _due(value: Any, field: str = "due_date") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} is not a valid calendar date") from error
    return value


def _id(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


class ApplicationCRM:
    """An isolated local CRM using the same connection and job IDs as Store."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.conn = store.connection
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS crm_stage_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                stage TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_stages_job
                ON crm_stage_events(job_id, event_id DESC);

            CREATE TABLE IF NOT EXISTS crm_stage_corrections (
                corrected_event_id INTEGER PRIMARY KEY REFERENCES crm_stage_events(event_id),
                correction_event_id INTEGER NOT NULL UNIQUE REFERENCES crm_stage_events(event_id)
            );

            CREATE TABLE IF NOT EXISTS crm_priority_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                priority TEXT NOT NULL CHECK(priority IN ('none','low','medium','high')),
                actor TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_priority_job
                ON crm_priority_events(job_id, event_id DESC);
            CREATE TABLE IF NOT EXISTS crm_interview_notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                interview_date TEXT NOT NULL,
                round TEXT NOT NULL,
                notes TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_interviews_job
                ON crm_interview_notes(job_id, interview_date DESC, note_id DESC);

            CREATE TABLE IF NOT EXISTS crm_contacts (
                contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_contacts_job ON crm_contacts(job_id);

            CREATE TABLE IF NOT EXISTS crm_followups (
                followup_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                contact_id INTEGER REFERENCES crm_contacts(contact_id),
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_followups_due
                ON crm_followups(completed_at,due_date);

            CREATE TABLE IF NOT EXISTS crm_followup_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                followup_id INTEGER NOT NULL REFERENCES crm_followups(followup_id),
                action TEXT NOT NULL,
                due_date TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_crm_followup_events_task_action
                ON crm_followup_events(followup_id, action);
            """
        )

    def _stage(self, job_id: str) -> str:
        self.store.get_job(job_id)
        row = self.conn.execute(
            "SELECT stage FROM crm_stage_events WHERE job_id=? ORDER BY event_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        submitted = self.store.stage_of(job_id) == "submitted"
        if submitted:
            return row["stage"] if row and row["stage"] in POST_APPLICATION else "applied"
        return row["stage"] if row and row["stage"] in PRE_APPLICATION else "discovered"

    def allowed(self, job_id: str) -> tuple[str, ...]:
        stage = self._stage(job_id)
        return (POST_APPLICATION if stage in POST_APPLICATION else PRE_APPLICATION)[stage]

    def change_stage(self, job_id: str, stage: Any, note: Any = "") -> dict:
        """Append a candidate-authorized transition, never create a submission."""
        if not isinstance(stage, str):
            raise ValueError("stage must be a string")
        comment = _text(note, "note", 1000)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._stage(job_id)
            allowed = self.allowed(job_id)
            if stage not in allowed:
                raise ValueError(f"Cannot move from {current} to {stage}; allowed: {', '.join(allowed) or 'none'}")
            cursor = self.conn.execute(
                "INSERT INTO crm_stage_events(job_id,stage,note) VALUES(?,?,?)",
                (job_id, stage, comment),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {"event_id": cursor.lastrowid, "job_id": job_id, "stage": stage, "note": comment}

    def stage_history(self, job_id: str) -> list[dict]:
        self.store.get_job(job_id)
        rows = self.conn.execute(
            "SELECT e.event_id,e.job_id,e.stage,e.note,e.actor,e.created_at, "
            "c.corrected_event_id AS corrects_event_id, d.correction_event_id AS corrected_by_event_id "
            "FROM crm_stage_events e LEFT JOIN crm_stage_corrections c ON c.correction_event_id=e.event_id "
            "LEFT JOIN crm_stage_corrections d ON d.corrected_event_id=e.event_id "
            "WHERE e.job_id=? ORDER BY e.event_id DESC LIMIT 100",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def correction_option(self, job_id: str) -> dict | None:
        if self.store.stage_of(job_id) != 'submitted':
            return None
        latest = self.conn.execute('SELECT event_id,stage FROM crm_stage_events WHERE job_id=? ORDER BY event_id DESC LIMIT 1', (job_id,)).fetchone()
        if not latest or latest['stage'] not in TERMINAL_STAGES:
            return None
        prior = self.conn.execute("SELECT stage FROM crm_stage_events WHERE job_id=? AND event_id<? AND stage IN ('applied','screening','assessment','interview','offer') ORDER BY event_id DESC LIMIT 1", (job_id, latest['event_id'])).fetchone()
        return {'event_id': latest['event_id'], 'from_stage': latest['stage'], 'restore_stage': prior['stage'] if prior else 'applied'}

    def correct_terminal(self, job_id: str, expected_event_id: Any, reason: Any, acknowledged: Any) -> dict:
        """Append a correction and restore the previous post-submission stage."""
        _id(expected_event_id, 'expected_event_id')
        comment = _text(reason, 'reason', 1000, required=True)
        if acknowledged is not True:
            raise ValueError('Explicit acknowledgement is required to correct an outcome')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            self.store.get_job(job_id)
            option = self.correction_option(job_id)
            if not option or option['event_id'] != expected_event_id:
                raise StageConflict('The outcome changed; reload before correcting it')
            cursor = self.conn.execute('INSERT INTO crm_stage_events(job_id,stage,note) VALUES(?,?,?)',
                                       (job_id, option['restore_stage'], comment))
            self.conn.execute('INSERT INTO crm_stage_corrections(corrected_event_id,correction_event_id) VALUES(?,?)',
                              (expected_event_id, cursor.lastrowid))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {'job_id': job_id, 'stage': option['restore_stage'], 'corrected_event_id': expected_event_id,
                'event_id': cursor.lastrowid}

    def add_contact(self, job_id: str, name: Any, role: Any = "",
                    email: Any = "", notes: Any = "") -> dict:
        self.store.get_job(job_id)
        full_name = _text(name, "name", 120, required=True)
        role = _text(role, "role", 120)
        email = _text(email, "email", 254)
        notes = _text(notes, "notes", 1000)
        if email and not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("email must be a valid email address or empty")
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO crm_contacts(job_id,name,role,email,notes) VALUES(?,?,?,?,?)",
                (job_id, full_name, role, email, notes),
            )
        return {"contact_id": cursor.lastrowid, "job_id": job_id, "name": full_name}

    def update_contact(self, job_id: str, contact_id: Any, name: Any,
                       role: Any = "", email: Any = "", notes: Any = "") -> dict:
        self.store.get_job(job_id)
        contact_id = _id(contact_id, "contact_id")
        name = _text(name, "name", 120, required=True)
        role = _text(role, "role", 120)
        email = _text(email, "email", 254)
        notes = _text(notes, "notes", 1000)
        if email and not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("email must be a valid email address or empty")
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE crm_contacts SET name=?,role=?,email=?,notes=? "
                "WHERE contact_id=? AND job_id=?",
                (name, role, email, notes, contact_id, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Recruiter contact does not belong to this opportunity")
        return {"contact_id": contact_id, "job_id": job_id, "name": name}

    def contacts(self, job_id: str) -> list[dict]:
        self.store.get_job(job_id)
        return [dict(row) for row in self.conn.execute(
            "SELECT contact_id,name,role,email,notes,created_at FROM crm_contacts "
            "WHERE job_id=? ORDER BY contact_id DESC LIMIT 100",
            (job_id,),
        ).fetchall()]

    def _contact_for_job(self, job_id: str, contact_id: Any) -> int | None:
        if contact_id is None:
            return None
        contact_id = _id(contact_id, "contact_id")
        row = self.conn.execute(
            "SELECT contact_id FROM crm_contacts WHERE contact_id=? AND job_id=?",
            (contact_id, job_id),
        ).fetchone()
        if row is None:
            raise ValueError("Recruiter contact does not belong to this opportunity")
        return contact_id

    def add_followup(self, job_id: str, title: Any, due_date: Any,
                     contact_id: Any = None) -> dict:
        self.store.get_job(job_id)
        title = _text(title, "title", 200, required=True)
        due_date = _due(due_date)
        contact_id = self._contact_for_job(job_id, contact_id)
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO crm_followups(job_id,contact_id,title,due_date) VALUES(?,?,?,?)",
                (job_id, contact_id, title, due_date),
            )
            self.conn.execute(
                "INSERT INTO crm_followup_events(followup_id,action,due_date) VALUES(?,?,?)",
                (cursor.lastrowid, "created", due_date),
            )
        return {"followup_id": cursor.lastrowid, "job_id": job_id, "title": title, "due_date": due_date}

    def _followup(self, followup_id: Any):
        followup_id = _id(followup_id, "followup_id")
        row = self.conn.execute(
            "SELECT * FROM crm_followups WHERE followup_id=?", (followup_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Follow-up task not found")
        return row

    def update_followup(self, followup_id: Any, *,
                        complete: bool = False, cancel: bool = False, due_date: Any = None) -> dict:
        if type(complete) is not bool or type(cancel) is not bool or sum((complete, cancel, due_date is not None)) != 1:
            raise ValueError("Choose exactly one action: complete, cancel or reschedule")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._followup(followup_id)
            if row["completed_at"] is not None:
                raise ValueError("Completed follow-up tasks cannot be changed")
            if self.conn.execute("SELECT 1 FROM crm_followup_events WHERE followup_id=? AND action='cancelled'", (row["followup_id"],)).fetchone():
                raise ValueError("Cancelled follow-up tasks cannot be changed")
            new_date = _due(due_date) if due_date is not None else row["due_date"]
            action = "completed" if complete else "cancelled" if cancel else "rescheduled"
            if complete:
                self.conn.execute(
                    "UPDATE crm_followups SET completed_at=CURRENT_TIMESTAMP WHERE followup_id=?",
                    (row["followup_id"],),
                )
            elif not cancel:
                self.conn.execute(
                    "UPDATE crm_followups SET due_date=? WHERE followup_id=?",
                    (new_date, row["followup_id"]),
                )
            self.conn.execute(
                "INSERT INTO crm_followup_events(followup_id,action,due_date) VALUES(?,?,?)",
                (row["followup_id"], action, new_date),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {"followup_id": row["followup_id"], "action": action, "due_date": new_date}

    def followups(self, job_id: str) -> list[dict]:
        self.store.get_job(job_id)
        rows = self.conn.execute(
            "SELECT f.followup_id,f.title,f.due_date,f.completed_at,f.created_at,"
            " f.contact_id,c.name AS contact_name,"
            " EXISTS(SELECT 1 FROM crm_followup_events e WHERE e.followup_id=f.followup_id AND e.action='cancelled') AS cancelled FROM crm_followups f"
            " LEFT JOIN crm_contacts c ON c.contact_id=f.contact_id"
            " WHERE f.job_id=? ORDER BY (f.completed_at IS NOT NULL OR cancelled),f.due_date LIMIT 100",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def priority(self, job_id: str) -> str:
        self.store.get_job(job_id)
        row = self.conn.execute(
            "SELECT priority FROM crm_priority_events WHERE job_id=? ORDER BY event_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return row["priority"] if row else "none"

    def set_priority(self, job_id: str, priority: Any) -> dict:
        self.store.get_job(job_id)
        if not isinstance(priority, str) or priority not in ("none", "low", "medium", "high"):
            raise ValueError("priority must be none, low, medium or high")
        with self.conn:
            self.conn.execute(
                "INSERT INTO crm_priority_events(job_id,priority) VALUES(?,?)", (job_id, priority),
            )
        return {"job_id": job_id, "priority": priority}

    def add_interview_note(self, job_id: str, interview_date: Any, round: Any, notes: Any) -> dict:
        self.store.get_job(job_id)
        interview_date = _due(interview_date, "interview_date")
        round = _text(round, "round", 120, required=True)
        notes = _text(notes, "notes", 4000, required=True)
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO crm_interview_notes(job_id,interview_date,round,notes) VALUES(?,?,?,?)",
                (job_id, interview_date, round, notes),
            )
        return {"note_id": cursor.lastrowid, "job_id": job_id}

    def interview_notes(self, job_id: str) -> list[dict]:
        self.store.get_job(job_id)
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM crm_interview_notes WHERE job_id=? ORDER BY interview_date DESC,note_id DESC",
            (job_id,),
        ).fetchall()]

    def details(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        return {
            "job_id": job_id,
            "job": {"id": job.id, "title": job.title, "company": job.company},
            "stage": self._stage(job_id),
            "allowed_transitions": list(self.allowed(job_id)),
            "application_submitted": self.store.stage_of(job_id) == "submitted",
            "stage_events": self.stage_history(job_id),
            "correction_option": self.correction_option(job_id),
            "contacts": self.contacts(job_id),
            "followups": self.followups(job_id),
            "priority": self.priority(job_id),
            "priority_events": [dict(row) for row in self.conn.execute(
                "SELECT * FROM crm_priority_events WHERE job_id=? ORDER BY event_id DESC", (job_id,),
            ).fetchall()],
            "interview_notes": self.interview_notes(job_id),
        }

    def export_history(self) -> dict:
        """Export a consistent, uncapped tracker snapshot, not the candidate vault.

        Explicit columns keep future private tables/fields out of this format.
        IDs link the normalized collections; event IDs preserve recorded order.
        """
        queries = {
            "jobs": "SELECT job_id,company,title,location,url,description,source,remote FROM jobs ORDER BY job_id",
            "applications": "SELECT job_id,stage,receipt,updated_at FROM applications ORDER BY job_id",
            "reviews": "SELECT review_id,job_id,packet_sha256,decision,decided_at FROM packet_reviews ORDER BY review_id",
            "stage_events": "SELECT event_id,job_id,stage,note,actor,created_at FROM crm_stage_events ORDER BY event_id",
            "stage_corrections": "SELECT corrected_event_id,correction_event_id FROM crm_stage_corrections ORDER BY correction_event_id",
            "priority_events": "SELECT event_id,job_id,priority,actor,created_at FROM crm_priority_events ORDER BY event_id",
            "contacts": "SELECT contact_id,job_id,name,role,email,notes,created_at FROM crm_contacts ORDER BY contact_id",
            "interview_notes": "SELECT note_id,job_id,interview_date,round,notes,actor,created_at FROM crm_interview_notes ORDER BY note_id",
            "followups": "SELECT followup_id,job_id,contact_id,title,due_date,completed_at,created_at FROM crm_followups ORDER BY followup_id",
            "followup_events": "SELECT event_id,followup_id,action,due_date,actor,created_at FROM crm_followup_events ORDER BY event_id",
        }
        with self.conn:
            self.conn.execute("BEGIN")
            records = {name: [dict(row) for row in self.conn.execute(query)]
                       for name, query in queries.items()}
        return {
            "format": "career-agent-tracker-history", "schema_version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }

    def outcomes(self) -> dict:
        """Count distinct attested applications with explicitly recorded milestones."""
        rows = self.conn.execute(
            "SELECT a.job_id,e.stage FROM applications a "
            "LEFT JOIN crm_stage_events e ON e.job_id=a.job_id "
            "WHERE a.stage='submitted' AND (e.event_id IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM crm_stage_corrections c WHERE c.corrected_event_id=e.event_id))"
        ).fetchall()
        submitted = {row["job_id"] for row in rows}
        interviews = {row["job_id"] for row in rows if row["stage"] == "interview"}
        offers = {row["job_id"] for row in rows if row["stage"] == "offer"}
        accepted = {row["job_id"] for row in rows if row["stage"] == "accepted"}
        total = len(submitted)
        return {
            "submitted": total, "interviewed": len(interviews),
            "offered": len(offers), "accepted": len(accepted),
            "interview_rate": round(100 * len(interviews) / total, 1) if total else None,
            "offer_rate": round(100 * len(offers) / total, 1) if total else None,
        }

    def summary(self) -> dict:
        jobs = self.store.list_jobs(limit=-1)
        counts: dict[str, int] = {}
        job_stages = []
        for job in jobs:
            stage = self._stage(job.id)
            job_stages.append({
                "job_id": job.id, "stage": stage, "title": job.title,
                "company": job.company, "location": job.location,
                "application_stage": self.store.stage_of(job.id),
                "priority": self.priority(job.id),
            })
            counts[stage] = counts.get(stage, 0) + 1
        rows = self.conn.execute(
            "SELECT f.followup_id,f.job_id,f.title,f.due_date,"
            " j.company,j.title AS job_title "
            "FROM crm_followups f JOIN jobs j ON j.job_id=f.job_id "
            "WHERE f.completed_at IS NULL AND NOT EXISTS (SELECT 1 FROM crm_followup_events e WHERE e.followup_id=f.followup_id AND e.action='cancelled') ORDER BY f.due_date,f.followup_id LIMIT 50"
        ).fetchall()
        today = date.today().isoformat()
        upcoming = [{**dict(row), "overdue": row["due_date"] < today} for row in rows]
        return {
            "stages": counts, "job_stages": job_stages,
            "open_followups": upcoming, "today": today,
            "outcomes": self.outcomes(),
        }
