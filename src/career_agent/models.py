"""Normalized public job models and local candidate profile preferences."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def as_text(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "none", "<na>"} else result


def first_text(record: dict[str, object], *keys: str) -> str:
    """Choose the first nonmissing textual field without testing scalar truthiness."""
    for key in keys:
        value = as_text(record.get(key))
        if value:
            return value
    return ""


def posting_date(value: object) -> str | None:
    """Accept an unambiguous source date only; do not infer from relative text or timestamps."""
    value = as_text(value)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.year >= 1900 else None


def canonical_url(value: str) -> str:
    """Keep job IDs in query parameters but strip common tracking fields."""
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("A job must have an absolute HTTP(S) URL")
    if parts.username or parts.password:
        raise ValueError("Job URLs must not include embedded credentials")
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"ref", "trk", "trackingid"}
    )
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/") or "/",
        urlencode(query),
        "",
    ))


@dataclass(frozen=True)
class Job:
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    source: str = "csv"
    remote: bool = False
    reported_posted_date: str | None = None
    posted_date_status: str = 'missing'

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical_url(self.url).encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, record: dict[str, object], source: str = "csv") -> "Job":
        company = first_text(record, "company", "company_name")
        title = first_text(record, "title", "job_title")
        url = first_text(record, "job_url", "url", "job_url_direct")
        if not company or not title:
            raise ValueError("Job requires company and title")
        raw_remote = record.get("is_remote", record.get("remote", False))
        remote = raw_remote is True or str(raw_remote).strip().lower() in {"1", "true", "yes"}
        posted = posting_date(record.get('reported_posted_date', record.get('date_posted')))
        status = as_text(record.get('posted_date_status'))
        status = (status if status in ('partial', 'conflicting') and (posted is not None or status == 'conflicting')
                  else 'reported' if posted else 'missing')
        if status == 'conflicting':
            posted = None
        return cls(
            company=company,
            title=title,
            location=as_text(record.get("location")),
            url=canonical_url(url),
            description=as_text(record.get("description")),
            source=first_text(record, "site", "source") or source,
            remote=remote,
            reported_posted_date=posted, posted_date_status=status,
        )


def merge_discovery_rows(jobs: list[Job], *, prefer_last: bool = False) -> list[Job]:
    """Collapse repeated job/source rows without hiding disagreement about dates."""
    grouped: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        grouped.setdefault((job.id, job.source), []).append(job)
    merged = []
    for rows in grouped.values():
        dates = {row.reported_posted_date for row in rows if row.reported_posted_date}
        conflict = len(dates) > 1 or any(row.posted_date_status == 'conflicting' for row in rows)
        incomplete = any(row.posted_date_status in ('missing', 'partial') for row in rows)
        status = 'conflicting' if conflict else 'missing' if not dates else 'partial' if incomplete else 'reported'
        merged.append(replace(rows[-1] if prefer_last else rows[0], reported_posted_date=next(iter(dates)) if len(dates) == 1 and not conflict else None,
                              posted_date_status=status))
    return merged


@dataclass(frozen=True)
class CandidateProfile:
    target_titles: tuple[str, ...]
    skills: tuple[str, ...]
    preferred_locations: tuple[str, ...]
    allow_remote: bool = True

    @classmethod
    def load(cls, path: str | Path) -> "CandidateProfile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Profile must be a JSON object")

        def string_list(name: str) -> tuple[str, ...]:
            value = data.get(name, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{name} must be a list of strings")
            return tuple(v.strip() for v in value if v.strip())

        if not isinstance(data.get("allow_remote", True), bool):
            raise ValueError("allow_remote must be boolean")
        return cls(
            target_titles=string_list("target_titles"),
            skills=string_list("skills"),
            preferred_locations=string_list("preferred_locations"),
            allow_remote=data.get("allow_remote", True),
        )
