"""Normalized public job models and local candidate profile preferences."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def as_text(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "none", "<na>"} else result


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

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical_url(self.url).encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, record: dict[str, object], source: str = "csv") -> "Job":
        company = as_text(record.get("company") or record.get("company_name"))
        title = as_text(record.get("title") or record.get("job_title"))
        url = as_text(record.get("job_url") or record.get("url") or record.get("job_url_direct"))
        if not company or not title:
            raise ValueError("Job requires company and title")
        raw_remote = record.get("is_remote", record.get("remote", False))
        remote = raw_remote is True or str(raw_remote).strip().lower() in {"1", "true", "yes"}
        return cls(
            company=company,
            title=title,
            location=as_text(record.get("location")),
            url=canonical_url(url),
            description=as_text(record.get("description")),
            source=as_text(record.get("site") or record.get("source")) or source,
            remote=remote,
        )


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
