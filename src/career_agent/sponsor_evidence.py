"""Source-backed employer filing history, separated from current role policy.

This adapter does not query government sites or verify uploaded records.
It consumes a candidate-supplied CSV, preserving provenance so that users
can independently inspect authoritative DOL/USCIS documents.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .models import Job
from .sponsorship import assess_sponsorship


def employer_key(name: str) -> str:
    """Conservative exact matching; do not silently merge subsidiaries or aliases."""
    return " ".join(name.casefold().split())


@dataclass(frozen=True)
class FilingEvidence:
    employer: str
    fiscal_year: int
    source_url: str
    source_type: str
    case_status: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str], line: int) -> "FilingEvidence":
        employer = (row.get("employer") or "").strip()
        source_url = (row.get("source_url") or "").strip()
        year = (row.get("fiscal_year") or row.get("record_year") or "").strip()
        source_type = (row.get("source_type") or "user-supplied").strip()
        if not employer:
            raise ValueError(f"Evidence row {line}: employer is required")
        parts = urlsplit(source_url)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
            raise ValueError(f"Evidence row {line}: absolute HTTP(S) source_url required")
        try:
            fiscal_year = int(year)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Evidence row {line}: valid fiscal_year required") from error
        if not 1990 <= fiscal_year <= 2100:
            raise ValueError(f"Evidence row {line}: fiscal_year out of range")
        return cls(
            employer=employer,
            fiscal_year=fiscal_year,
            source_url=source_url,
            source_type=source_type,
            case_status=(row.get("case_status") or "").strip(),
        )


def load_filing_evidence(path: str | Path | None) -> tuple[FilingEvidence, ...]:
    if path is None:
        return ()
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not {"employer", "source_url"}.issubset(reader.fieldnames or []):
            raise ValueError("Evidence CSV needs employer, source_url and fiscal_year columns")
        return tuple(FilingEvidence.from_row(row, line) for line, row in enumerate(reader, 2))


def sponsorship_report(job: Job, records: tuple[FilingEvidence, ...] = ()) -> dict:
    """Expose document links without claiming independent verification."""
    matches = [record for record in records if employer_key(record.employer) == employer_key(job.company)]
    assessment = assess_sponsorship(
        job, {job.company.casefold()} if matches else set()
    )
    return {
        "status": assessment.status,
        "reason": assessment.reason,
        "posting_url": job.url,
        "historical_records": [asdict(record) for record in sorted(
            matches, key=lambda record: record.fiscal_year, reverse=True
        )],
        "history_verification": "user_supplied_not_independently_verified",
        "job_policy_verification": "keyword_signal_only",
        "disclaimer": (
            "An LCA or petition filing is not a job-level sponsorship offer, "
            "and no immigration eligibility determination has been made."
        ),
    }
