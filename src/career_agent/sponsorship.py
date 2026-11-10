"""Conservative job-level and historical employer sponsorship signals."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Job

EXCLUSIONS = (
    r"\bno (?:visa )?sponsorship\b",
    r"\b(?:do not|does not|cannot|can't|will not|unable to) (?:offer|provide|support)?\s*(?:visa )?sponsorship\b",
    r"\bwithout (?:current or future )?(?:visa )?sponsorship\b",
    r"\bsponsorship (?:is )?not available\b",
    r"\bmust be (?:legally )?authorized to work (?:in the (?:united states|us|u\.s\.))? without (?:visa )?sponsorship\b",
)
POSITIVES = (
    r"\bvisa sponsorship (?:is )?(?:available|provided|offered)\b",
    r"\bwe (?:will|can) sponsor\b",
    r"\bh-1b sponsorship (?:is )?(?:available|provided|offered)\b",
)


@dataclass(frozen=True)
class Sponsorship:
    status: str
    reason: str


def load_historical_evidence(path: str | Path | None) -> set[str]:
    """Accept employer names only with a cited source URL; no sponsorship inference."""
    if path is None:
        return set()
    employers = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            employer = (row.get("employer") or "").strip()
            source = (row.get("source_url") or "").strip()
            if employer and source.startswith(("https://", "http://")):
                employers.add(employer.casefold())
    return employers


def assess_sponsorship(job: Job, historical_employers: set[str] | None = None) -> Sponsorship:
    text = job.description.lower()
    if any(re.search(pattern, text) for pattern in EXCLUSIONS):
        return Sponsorship(
            "explicit_exclusion",
            "Posting appears to exclude sponsorship; verify details with the employer.",
        )
    if any(re.search(pattern, text) for pattern in POSITIVES):
        return Sponsorship(
            "posting_claims_sponsorship",
            "Posting mentions sponsorship; written confirmation for this job is still necessary.",
        )
    if job.company.casefold() in (historical_employers or set()):
        return Sponsorship(
            "historical_only",
            "Historical employer filing evidence does not establish sponsorship for this role.",
        )
    return Sponsorship("unknown", "No verified job-level sponsorship commitment found.")
