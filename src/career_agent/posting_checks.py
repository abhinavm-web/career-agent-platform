"""Read-only checks of exact public ATS posting identities; never submit forms."""
from __future__ import annotations

import ipaddress
from html.parser import HTMLParser
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .public_sources import public_get, SourceUnavailable
from .sponsor_evidence import employer_key
from .sponsorship import h1b_policy


def target(url):
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != 'https' or parts.username or parts.password or port not in (None, 443):
        return None
    if parts.hostname in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        match = re.fullmatch(r'/([A-Za-z0-9_-]+)/jobs/([0-9]+)/?', parts.path)
        if match:
            board, job_id = match.groups()
            return 'greenhouse', job_id, f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}'
    if parts.hostname in {'jobs.lever.co', 'jobs.eu.lever.co'}:
        match = re.fullmatch(r'/([A-Za-z0-9_-]+)/([a-fA-F0-9-]{36})/?', parts.path)
        if match:
            board, job_id = match.groups()
            api_host = 'api.eu.lever.co' if parts.hostname == 'jobs.eu.lever.co' else 'api.lever.co'
            return 'lever', job_id, f'https://{api_host}/v0/postings/{board}/{job_id}?mode=json'
    return None


def website_target(url):
    """Only anonymous public HTTPS targets; public_get also pins and validates DNS."""
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.port not in (None, 443)
                or parts.username or parts.password or parts.fragment or '..' in parts.path.split('/')
                or any(ord(c) < 33 for c in url)):
            return None
        host = parts.hostname.lower()
        if '.' not in host or host.endswith(('.localhost', '.local', '.invalid', '.test')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return host
    except ValueError:
        return None


class _PostingHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs).get('type', '').lower() == 'application/ld+json'
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.documents.append(''.join(self.parts))
            self.active = False


def check_website(job, result):
    host = website_target(job.url)
    if not host:
        return result
    result['provider'] = 'website'
    try:
        status, content, content_type = public_get(job.url, {host})
        result['http_status'] = status
        if status != 200:
            result['reason'] = f'Website returned HTTP {status}. Redirects, access restrictions and missing pages cannot confirm whether this role is still open. Open the original posting to review.'
            return result
        if 'text/html' not in content_type.lower():
            result['reason'] = 'Website responded, but did not return a readable HTML job posting.'
            return result
        result.update(availability='page_reachable', reason='The page responded, but the exact vacancy could not be verified. It may require sign-in or JavaScript. Open the original posting to confirm.')
        parser = _PostingHTML()
        parser.feed(content.decode('utf-8'))
        postings = []
        for raw in parser.documents:
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                candidates = [node] + (node.get('@graph', []) if isinstance(node.get('@graph'), list) else [])
                for candidate in candidates:
                    if isinstance(candidate, dict) and candidate.get('@type') == 'JobPosting':
                        postings.append(candidate)
        if len(postings) != 1:
            return result
        posting = postings[0]
        employer = posting.get('hiringOrganization')
        # Require a source-declared exact URL, title and employer, not a generic page match.
        if (posting.get('url') != job.url or not isinstance(posting.get('title'), str)
                or employer_key(posting['title']) != employer_key(job.title)
                or not isinstance(employer, dict) or not isinstance(employer.get('name'), str)
                or employer_key(employer['name']) != employer_key(job.company)
                or not isinstance(posting.get('description'), str) or not posting['description'].strip()):
            return result
        deadline = posting.get('validThrough')
        if deadline is not None:
            if not isinstance(deadline, str) or 'T' not in deadline:
                return result
            expires = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
            if expires.tzinfo is None:
                return result
            result['application_deadline'] = deadline
            if expires <= datetime.now(timezone.utc):
                result.update(availability='expired', reason='The matching website posting reports an application deadline that has passed.')
                return result
        result.update(availability='listed', reason='Website JobPosting data matches the saved URL, title and employer. Listed at check time; this does not guarantee active hiring or sponsorship.',
                      content_sha256=hashlib.sha256(content).hexdigest())
    except (SourceUnavailable, ValueError, TypeError, UnicodeError, RecursionError):
        result.update(availability='unknown', reason='Website could not be verified securely or returned invalid posting data. Open the original posting to review.')
    return result


def check_posting(job):
    result = {'availability': 'unknown', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'source_url': job.url, 'provider': None, 'http_status': None,
              'policy': None, 'reason': 'This URL cannot be checked automatically. Open the original posting to confirm availability.'}
    resolved = target(job.url)
    if not resolved:
        return check_website(job, result)
    provider, expected_id, endpoint = resolved
    result.update(provider=provider, source_url=endpoint)
    try:
        status, content, content_type = public_get(endpoint, {'boards-api.greenhouse.io', 'api.lever.co', 'api.eu.lever.co'})
        result['http_status'] = status
        if status != 200:
            result['reason'] = f'Provider returned HTTP {status}. Removal, blocking or a failed lookup cannot establish current availability.'
            return result
        if 'json' not in content_type.lower():
            raise ValueError('Provider did not return JSON')
        data = json.loads(content)
        if not isinstance(data, dict) or str(data.get('id')) != expected_id:
            raise ValueError('Provider returned a different or missing posting identity')
        title = data.get('title') if provider == 'greenhouse' else data.get('text')
        if not isinstance(title, str) or employer_key(title) != employer_key(job.title):
            raise ValueError('Posting title differs from the stored role; inspect before using this result')
        if provider == 'greenhouse':
            # Prospect/general-interest posts are not a specific vacancy.
            if 'internal_job_id' not in data:
                raise ValueError('Provider response is missing the vacancy identity')
            if data['internal_job_id'] is None:
                result['availability'] = 'general_interest'
                result['reason'] = 'Provider identifies this as a prospect/general-interest post, not a specific vacancy.'
                return result
            if type(data['internal_job_id']) is not int or data['internal_job_id'] < 1:
                raise ValueError('Provider response has invalid vacancy identity')
            company = data.get('company_name')
            if company is not None and not isinstance(company, str):
                raise ValueError('Provider response has invalid company name')
            if company and employer_key(company) != employer_key(job.company):
                raise ValueError('Provider company name differs from the stored employer; entity match needs review')
            description = data.get('content')
        else:
            description = data.get('descriptionPlain')
            if not isinstance(description, str):
                raise ValueError('Provider response is missing the posting description')
            lists = data.get('lists', [])
            if not isinstance(lists, list) or any(not isinstance(item, dict) or not isinstance(item.get('content'), str) for item in lists):
                raise ValueError('Provider response has invalid description sections')
            extra = data.get('additionalPlain', '')
            if not isinstance(extra, str):
                raise ValueError('Provider response has invalid additional text')
            description += '\n' + '\n'.join(item['content'] for item in lists) + '\n' + extra
        if not isinstance(description, str) or not description.strip():
            raise ValueError('Provider returned an empty description')
        # Greenhouse content may be HTML-escaped HTML. Unescape before stripping tags.
        description = html.unescape(description)
        policy = h1b_policy(description)
        policy.pop('potential_match', None)  # Live observation is not a candidate eligibility decision.
        result.update(title=title, content_sha256=hashlib.sha256(description.encode()).hexdigest(), policy=policy)
        deadline = data.get('application_deadline')
        if deadline:
            if not isinstance(deadline, str):
                raise ValueError('Invalid provider application deadline')
            expires = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
            if expires.tzinfo is None:
                raise ValueError('Provider deadline has no time zone; availability uncertain')
            result['application_deadline'] = deadline
            if expires <= datetime.now(timezone.utc):
                result.update(availability='expired', reason='Provider deadline has passed, even though the posting remains listed.')
                return result
        result.update(availability='listed', reason='Exact posting ID and title were listed by the public ATS at check time. This is not a guarantee of active hiring or H-1B sponsorship.')
    except (SourceUnavailable, ValueError, TypeError, UnicodeError) as error:
        result.update(availability='unknown', policy=None, reason=str(error))
    return result


class PostingChecks:
    def __init__(self, store):
        self.conn = store.connection
        self.conn.execute('''CREATE TABLE IF NOT EXISTS posting_checks (
            check_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(job_id), result_json TEXT NOT NULL
        )''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS posting_checks_job ON posting_checks(job_id,check_id DESC)')

    def save(self, job_id, result):
        with self.conn:
            self.conn.execute('INSERT INTO posting_checks(job_id,result_json) VALUES(?,?)', (job_id, json.dumps(result)))
        return result

    def latest(self, job_id):
        row = self.conn.execute('SELECT result_json FROM posting_checks WHERE job_id=? ORDER BY check_id DESC LIMIT 1', (job_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row['result_json'])
        try:
            observed_at = datetime.fromisoformat(result.get('checked_at'))
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError('Timestamp has no timezone')
            age = (datetime.now(timezone.utc) - observed_at).total_seconds()
            result['stale'] = age > 86400 or age < 0
        except (ValueError, TypeError, OverflowError):
            result['stale'] = True
        if result['stale']:
            result['observed_availability'] = result.get('availability', 'unknown')
            result['availability'] = 'unknown'
            result['reason'] = 'Previous observation is stale or has an invalid timestamp; check again. ' + str(result.get('reason', ''))
        elif result.get('availability') == 'listed' and result.get('application_deadline') is not None:
            try:
                raw_deadline = result['application_deadline']
                if not isinstance(raw_deadline, str) or 'T' not in raw_deadline:
                    raise ValueError('Deadline requires a time and timezone')
                deadline = datetime.fromisoformat(raw_deadline.replace('Z', '+00:00'))
                if deadline.tzinfo is None or deadline.utcoffset() is None:
                    raise ValueError('Deadline requires a time and timezone')
                if deadline <= datetime.now(timezone.utc):
                    result['observed_availability'] = 'listed'
                    result['availability'] = 'expired'
                    result['reason'] = 'The provider-reported application deadline has passed since the check. Current availability is unknown; verify the posting directly.'
            except (ValueError, TypeError, OverflowError):
                result['observed_availability'] = 'listed'
                result['availability'] = 'unknown'
                result['reason'] = 'The recorded application deadline is invalid or lacks a timezone; current availability is unknown.'
        return result
