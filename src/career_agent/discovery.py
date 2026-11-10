"""Job imports and optional explicitly invoked US job-board discovery."""
from __future__ import annotations

import csv
from pathlib import Path

from .models import Job


class DiscoveryUnavailable(RuntimeError):
    """The optional discovery dependency is not installed."""


def read_csv_jobs(path: str | Path) -> list[Job]:
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return [Job.from_mapping(row) for row in csv.DictReader(file)]


def discover_jobspy(query: str, location: str, results: int = 20) -> list[Job]:
    """No background discovery. Follow source terms and rate limits."""
    if not 1 <= results <= 100:
        raise ValueError("results must be between 1 and 100")
    try:
        from jobspy import scrape_jobs
    except ImportError as error:
        raise DiscoveryUnavailable('Install JobSpy: pip install -e ".[jobspy]"') from error
    frame = scrape_jobs(
        site_name=["indeed", "linkedin", "zip_recruiter"],
        search_term=query,
        location=location,
        results_wanted=results,
        country_indeed="USA",
    )
    jobs = []
    for raw in frame.to_dict(orient="records"):
        try:
            jobs.append(Job.from_mapping(raw, source="jobspy"))
        except ValueError:
            # Incomplete provider records are intentionally excluded.
            continue
    return jobs
