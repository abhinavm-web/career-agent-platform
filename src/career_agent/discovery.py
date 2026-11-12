"""Job imports and optional explicitly invoked US job-board discovery."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

from .models import Job


class DiscoveryUnavailable(RuntimeError):
    """The optional discovery dependency is not installed."""


class DiscoveryResults(list):
    """Rows plus sanitized source outcomes; no raw provider messages leave the worker."""
    def __init__(self, rows, source_status=None):
        super().__init__(rows)
        self.source_status = source_status or {}


def read_csv_jobs(path: str | Path) -> list[Job]:
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return [Job.from_mapping(row) for row in csv.DictReader(file)]


def discover_jobspy(query: str, location: str, results: int = 20, *, sites=None) -> list[Job]:
    """Explicit or user-scheduled discovery. Follow source terms and rate limits."""
    if not 1 <= results <= 100:
        raise ValueError("results must be between 1 and 100")
    try:
        from jobspy import scrape_jobs
    except ImportError as error:
        raise DiscoveryUnavailable('Install JobSpy: pip install -e ".[jobspy]"') from error
    selected = sites or ["indeed", "linkedin", "zip_recruiter"]
    frame = scrape_jobs(
        site_name=selected,
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
    attrs = getattr(frame, 'attrs', {})
    errors = attrs.get('errors', {}) if isinstance(attrs, dict) else {}
    warnings = attrs.get('warnings', {}) if isinstance(attrs, dict) else {}
    errors = errors if isinstance(errors, dict) else {}
    warnings = warnings if isinstance(warnings, dict) else {}
    status = {}
    for site in selected:
        count = sum(job.source == site for job in jobs)
        state = ('reported_error' if site in errors else 'reported_warning' if site in warnings
                 else 'returned' if count else 'inconclusive')
        status[site] = {'state': state, 'returned': count}
    return DiscoveryResults(jobs, status)


def discover_isolated(query: str, location: str, results: int, stop) -> list[Job]:
    """Run manual searches in the same bounded process used by saved searches."""
    from .automation import fetch
    if importlib.util.find_spec('jobspy') is None:
        raise DiscoveryUnavailable('Install JobSpy: pip install -e ".[jobspy]"')
    rows = fetch({'query': query, 'location': location, 'results': results,
                  'sites': ['indeed', 'linkedin', 'zip_recruiter']}, stop)
    return DiscoveryResults([Job.from_mapping(row) for row in rows], getattr(rows, 'source_status', {}))
