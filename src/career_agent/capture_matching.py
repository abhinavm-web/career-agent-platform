"""Limited, review-only URL comparisons for user-supplied browser captures."""
import re
from urllib.parse import parse_qsl, urlsplit

_LINKEDIN_JOB_PATH = re.compile(r'/jobs/view/(?:[a-z0-9]+(?:-[a-z0-9]+)*-)?([0-9]{1,20})/?\Z', re.I)
_LINKEDIN_TRACKING_KEYS = {'trk', 'trackingid', 'refid', 'ebp', 'alternatechannel',
                           'isjobsearch', 'skipredirect', 'position', 'pagenum'}


def linkedin_current_id_conflicts(url: str) -> bool:
    """Reject conflicting selected-job parameters even when complete URLs match."""
    try:
        parts = urlsplit(url)
        if parts.hostname not in {'linkedin.com', 'www.linkedin.com'}:
            return False
        match = _LINKEDIN_JOB_PATH.fullmatch(parts.path)
        if not match:
            return False
        ids = [value for key, value in parse_qsl(parts.query, keep_blank_values=True)
               if key.casefold() == 'currentjobid']
        return bool(ids) and (len(ids) != 1 or ids[0] != match.group(1))
    except ValueError:
        return True


def linkedin_job_number(url: str) -> str | None:
    """Recognize bounded job-view URLs; unknown or conflicting parameters stay ambiguous."""
    try:
        parts = urlsplit(url)
        if (parts.scheme.lower() != 'https' or parts.hostname not in {'linkedin.com', 'www.linkedin.com'}
                or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment):
            return None
        match = _LINKEDIN_JOB_PATH.fullmatch(parts.path)
        if not match:
            return None
        job_number = match.group(1)
        if linkedin_current_id_conflicts(url):
            return None
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            key = key.casefold()
            if key == 'currentjobid':
                continue
            elif key not in _LINKEDIN_TRACKING_KEYS and not key.startswith('utm_'):
                return None
        return job_number
    except ValueError:
        return None


def linkedin_capture_matches(saved_url: str, captured_url: str) -> bool:
    saved = linkedin_job_number(saved_url)
    return saved is not None and saved == linkedin_job_number(captured_url)
