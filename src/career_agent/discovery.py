"""Job imports and optional explicitly invoked US job-board discovery."""
from __future__ import annotations

import csv
import importlib.util
import logging
from pathlib import Path
from threading import Lock

from .models import Job


class DiscoveryUnavailable(RuntimeError):
    """The optional discovery dependency is not installed."""


class DiscoveryResults(list):
    """Rows plus sanitized source outcomes; no raw provider messages leave the worker."""
    def __init__(self, rows, source_status=None):
        super().__init__(rows)
        self.source_status = source_status or {}


_jobspy_lock = Lock()
SUPPORTED_SITES = ('indeed', 'linkedin', 'zip_recruiter')


def validate_sites(sites):
    if (not isinstance(sites, list) or not 1 <= len(sites) <= len(SUPPORTED_SITES)
            or not all(isinstance(site, str) and site in SUPPORTED_SITES for site in sites)
            or len(set(sites)) != len(sites)):
        raise ValueError('Choose one or more distinct supported job sources')
    return list(sites)


class _SkippedZipRecords(logging.Handler):
    """Record only that JobSpy skipped a ZipRecruiter row; never retain its message."""
    def __init__(self):
        super().__init__(logging.WARNING)
        self.skipped = False

    def emit(self, record):
        if isinstance(record.msg, str) and record.msg.startswith('skipping job:'):
            self.skipped = True


def read_csv_jobs(path: str | Path) -> list[Job]:
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return [Job.from_mapping(row) for row in csv.DictReader(file)]


def discover_jobspy(query: str, location: str, results: int = 20, *, sites=None, fetch_details=False) -> list[Job]:
    """Explicit or user-scheduled discovery. Follow source terms and rate limits."""
    if not 1 <= results <= 100:
        raise ValueError("results must be between 1 and 100")
    if type(fetch_details) is not bool or (fetch_details and results > 20):
        raise ValueError('Detail fetching requires a boolean and at most 20 results per source')
    try:
        from jobspy import scrape_jobs
    except ImportError as error:
        raise DiscoveryUnavailable('Install JobSpy: pip install -e ".[jobspy]"') from error
    selected = validate_sites(sites) if sites is not None else list(SUPPORTED_SITES)
    # JobSpy 1.4 can log skipped ZipRecruiter rows without putting them in
    # DataFrame.attrs['warnings']. Keep only the signal, never provider text.
    # Discovery normally runs in its own process; serialize direct calls too.
    with _jobspy_lock:
        zip_logger = logging.getLogger('jobspy.ziprecruiter')
        skipped = _SkippedZipRecords()
        zip_logger.addHandler(skipped)
        try:
            detail_option = {'fetch_description': True} if fetch_details else {}
            frame = scrape_jobs(
                site_name=selected,
                search_term=query,
                location=location,
                results_wanted=results,
                country_indeed="USA",
                **detail_option,
            )
        finally:
            zip_logger.removeHandler(skipped)
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
        state = ('reported_error' if site in errors else 'reported_warning' if site in warnings or (site == 'zip_recruiter' and skipped.skipped)
                 else 'returned' if count else 'inconclusive')
        status[site] = {'state': state, 'returned': count}
    return DiscoveryResults(jobs, status)


def discover_isolated(query: str, location: str, results: int, stop, *, sites=None, fetch_details=False) -> list[Job]:
    """Run manual searches in the same bounded process used by saved searches."""
    from .automation import fetch
    selected = validate_sites(sites) if sites is not None else list(SUPPORTED_SITES)
    if importlib.util.find_spec('jobspy') is None:
        raise DiscoveryUnavailable('Install JobSpy: pip install -e ".[jobspy]"')
    if type(fetch_details) is not bool or (fetch_details and results > 20):
        raise ValueError('Detail fetching requires a boolean and at most 20 results per source')
    settings = {'query': query, 'location': location, 'results': results, 'sites': selected}
    if fetch_details:
        settings['fetch_details'] = True
    rows = fetch(settings, stop)
    return DiscoveryResults([Job.from_mapping(row) for row in rows], getattr(rows, 'source_status', {}))
