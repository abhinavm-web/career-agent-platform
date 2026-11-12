"""Local H-1B LCA index. Imports are user-supplied, never independently verified."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .sponsor_evidence import employer_key
from .sponsorship import h1b_policy
from .storage import Store

DOL_URL = 'https://www.dol.gov/agencies/eta/foreign-labor/performance'
STATUSES = {'certified', 'certified-withdrawn', 'denied', 'withdrawn'}
XLSX_MAX_ROW = 1048576
XLSX_MAX_COLUMN = 1024


def _xlsx_dimensions(workbook, sheet, progress=None):
    """Inspect actual cell coordinates; worksheet dimension metadata can lie."""
    from defusedxml.sax import make_parser
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    from xml.sax.handler import ContentHandler, feature_namespaces
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

    class Limits(ContentHandler):
        def __init__(self):
            self.max_row = self.max_column = self.row_number = self.column_number = 0

        def startElementNS(self, name, _qualified_name, attributes):
            if name == (namespace, 'row'):
                raw = attributes.get((None, 'r'))
                if raw is not None and (not raw.isascii() or not raw.isdigit() or len(raw) > 7):
                    raise ValueError('Invalid worksheet row number')
                number = int(raw) if raw is not None else self.row_number + 1
                if not self.row_number < number <= XLSX_MAX_ROW:
                    raise ValueError('Worksheet row numbers must be ordered and within Excel limits')
                self.row_number, self.column_number = number, 0
                self.max_row = number
                if progress and number % 2000 == 0:
                    progress('scanning', number)
            elif name == (namespace, 'c'):
                raw = attributes.get((None, 'r'))
                previous_column = self.column_number
                if raw:
                    try:
                        column, number = coordinate_from_string(raw)
                        self.column_number = column_index_from_string(column)
                    except (ValueError, TypeError):
                        raise ValueError('Invalid worksheet cell coordinate') from None
                    if number != self.row_number:
                        raise ValueError('Worksheet cell does not match its row')
                else:
                    self.column_number += 1
                if not 1 <= self.column_number <= XLSX_MAX_COLUMN:
                    raise ValueError('Workbook has too many columns')
                if self.column_number <= previous_column:
                    raise ValueError('Worksheet cell columns must be ordered and distinct')
                self.max_column = max(self.max_column, self.column_number)

    limits = Limits()
    parser = make_parser()
    parser.setFeature(feature_namespaces, True)
    parser.setContentHandler(limits)
    with workbook._archive.open(sheet._worksheet_path) as stream:
        parser.parse(stream)
    return limits.max_row, limits.max_column


def fiscal_year(today=None):
    today = today or date.today()
    return today.year + (today.month >= 10)


def text(value, label, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label} must be nonempty text, at most {maximum} characters')
    return value.strip()


def validate_lca_source(source_url, captured_on):
    """Validate user-supplied provenance before accepting an import."""
    source_url = text(source_url, 'source_url', 2000)
    parts = urlsplit(source_url)
    try:
        valid_url = (parts.scheme == 'https' and parts.hostname in ('dol.gov', 'www.dol.gov')
                     and not parts.username and not parts.password and parts.port in (None, 443))
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError('Use the original HTTPS dol.gov source URL without credentials')
    try:
        captured = date.fromisoformat(captured_on)
    except (TypeError, ValueError):
        raise ValueError('captured_on must be YYYY-MM-DD') from None
    if captured.isoformat() != captured_on or captured > date.today():
        raise ValueError('captured_on must be a valid date no later than today')
    return source_url, captured


class H1BResearch:
    def __init__(self, store: Store):
        self.store = store
        self.conn = store.connection
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS h1b_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), need TEXT NOT NULL,
                min_year INTEGER NOT NULL, include_unknown INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS h1b_lca (
                case_number TEXT PRIMARY KEY, employer TEXT NOT NULL, employer_key TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL, decision_date TEXT NOT NULL, case_status TEXT NOT NULL,
                city TEXT NOT NULL, state TEXT NOT NULL, job_title TEXT NOT NULL,
                source_url TEXT NOT NULL, captured_on TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS h1b_employer ON h1b_lca(employer_key,fiscal_year);
            CREATE TABLE IF NOT EXISTS h1b_lca_imports (
                id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, captured_on TEXT NOT NULL,
                imported_at TEXT NOT NULL, selected_fields_sha256 TEXT NOT NULL,
                first_decision TEXT, last_decision TEXT, counts_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS h1b_employer_links (
                job_id TEXT NOT NULL REFERENCES jobs(job_id), revision INTEGER NOT NULL,
                employer TEXT NOT NULL, source_note TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(job_id,revision)
            );
        ''')
        from .uscis import USCISRecords
        from .posting_checks import PostingChecks
        self.uscis = USCISRecords(store)
        self.posting_checks = PostingChecks(store)

    def settings(self):
        row = self.conn.execute('SELECT need,min_year,include_unknown FROM h1b_settings WHERE id=1').fetchone()
        return {'need': row['need'], 'min_year': row['min_year'], 'include_unknown': bool(row['include_unknown'])} if row else {
            'need': 'any', 'min_year': fiscal_year() - 4, 'include_unknown': True}

    def configure(self, data):
        if not isinstance(data, dict) or set(data) != {'need', 'min_year', 'include_unknown'}:
            raise ValueError('Supply need, min_year and include_unknown')
        if data['need'] not in ('any', 'new', 'transfer') or not isinstance(data['need'], str):
            raise ValueError('need must be any, new or transfer')
        if type(data['min_year']) is not int or not 1990 <= data['min_year'] <= fiscal_year():
            raise ValueError('min_year must be between 1990 and the current federal fiscal year')
        if type(data['include_unknown']) is not bool:
            raise ValueError('include_unknown must be boolean')
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO h1b_settings VALUES(1,?,?,?)',
                              (data['need'], data['min_year'], int(data['include_unknown'])))
        return self.settings()

    def import_csv(self, stream, source_url, captured_on, *, progress=None):
        source_url, captured = validate_lca_source(source_url, captured_on)
        reader = csv.DictReader(stream, strict=True)
        required = {'CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'}
        if not required.issubset(reader.fieldnames or []) or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('Use DOL LCA CSV headers: ' + ', '.join(sorted(required)))
        counts = {'added': 0, 'updated': 0, 'duplicates': 0, 'older_skipped': 0, 'other_visas_skipped': 0}
        imported_at = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256()
        fields = sorted(required | {'EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE'})
        first_decision = last_decision = None
        row_count = 0
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            for line, row in enumerate(reader, 2):
                if None in row or any(row.get(key) is None for key in required):
                    raise ValueError(f'Row {line}: malformed CSV row')
                # Hash only fields that the importer examines, in source order. This
                # is an audit fingerprint, not a checksum of the original file.
                selected = [row.get(key) or '' for key in fields]
                digest.update((json.dumps(selected, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii'))
                row_count += 1
                if progress and row_count % 2000 == 0:
                    progress('reading', row_count)
                visa = text(row['VISA_CLASS'], f'Row {line} VISA_CLASS').upper()
                if visa in ('E-3 AUSTRALIAN', 'H-1B1 CHILE', 'H-1B1 SINGAPORE'):
                    counts['other_visas_skipped'] += 1
                    continue
                if visa != 'H-1B':
                    raise ValueError(f'Row {line}: unsupported VISA_CLASS')
                case = text(row['CASE_NUMBER'], f'Row {line} CASE_NUMBER', 80).upper()
                if not re.fullmatch(r'I-\d{3}-\d{5}-\d{6}', case):
                    raise ValueError(f'Row {line}: expected a public DOL LCA CASE_NUMBER')
                employer = text(row['EMPLOYER_NAME'], f'Row {line} EMPLOYER_NAME')
                # DOL's FY2026 Q3 workbook spells this value "Certified - Withdrawn";
                # its record-layout document uses "Certified-Withdrawn".
                status = re.sub(r'\s*-\s*', '-', text(row['CASE_STATUS'], f'Row {line} CASE_STATUS').casefold())
                if status not in STATUSES:
                    raise ValueError(f'Row {line}: unsupported CASE_STATUS')
                decision = None
                for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y-%m-%d %H:%M:%S'):
                    try:
                        decision = datetime.strptime(row['DECISION_DATE'].strip(), fmt).date()
                        break
                    except ValueError:
                        pass
                if decision is None or decision > captured or decision.year < 1990:
                    raise ValueError(f'Row {line}: invalid DECISION_DATE or later than capture date')
                first_decision = min(first_decision, decision) if first_decision else decision
                last_decision = max(last_decision, decision) if last_decision else decision
                optional = [text((row.get(k) or '').strip() or 'Unknown', f'Row {line} {k}') for k in ('EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE')]
                values = (case, employer, employer_key(employer), fiscal_year(decision), decision.isoformat(), status, *optional, source_url, captured_on, imported_at)
                previous = self.conn.execute('SELECT * FROM h1b_lca WHERE case_number=?', (case,)).fetchone()
                if previous:
                    if previous['captured_on'] > captured_on or previous['decision_date'] > decision.isoformat():
                        counts['older_skipped'] += 1
                        continue
                    old_content = tuple(previous[k] for k in ('case_number', 'employer', 'employer_key', 'fiscal_year', 'decision_date', 'case_status', 'city', 'state', 'job_title'))
                    if previous['captured_on'] == captured_on and old_content != values[:9]:
                        raise ValueError(f'Row {line}: conflicting record for the same case and capture date')
                    if tuple(previous)[:-1] == values[:-1]:
                        counts['duplicates'] += 1
                        continue
                self.conn.execute('INSERT OR REPLACE INTO h1b_lca VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', values)
                counts['updated' if previous else 'added'] += 1
            if not row_count:
                raise ValueError('The LCA disclosure file contains no data rows')
            self.conn.execute('INSERT INTO h1b_lca_imports (source_url,captured_on,imported_at,selected_fields_sha256,first_decision,last_decision,counts_json) VALUES(?,?,?,?,?,?,?)',
                              (source_url, captured_on, imported_at, digest.hexdigest(),
                               first_decision.isoformat() if first_decision else None,
                               last_decision.isoformat() if last_decision else None, json.dumps(counts)))
        return counts

    def import_xlsx(self, path, source_url, captured_on, *, progress=None):
        """Stream the relevant cells from a local DOL LCA disclosure workbook.

        This retains the existing CSV import's one-transaction validation and
        user-supplied provenance. It does not download or verify a DOL file.
        """
        try:
            from openpyxl import load_workbook
            from openpyxl.xml import DEFUSEDXML
            import defusedxml  # noqa: F401 - enables openpyxl's safe XML parser
        except ImportError:
            raise ValueError('Install workbook support: pip install -e ".[lca]"') from None
        if not DEFUSEDXML:
            raise ValueError('Workbook import requires openpyxl XML protections enabled')
        path = Path(path)
        if path.suffix.casefold() != '.xlsx' or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
            raise ValueError('Choose a local XLSX file no larger than 512 MB')
        workbook_file = None
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if (len(entries) > 5000 or sum(e.file_size for e in entries) > 4 * 1024 * 1024 * 1024
                        or len({e.filename for e in entries}) != len(entries)
                        or any(e.flag_bits & 1 for e in entries)
                        or any(e.filename == 'xl/sharedStrings.xml' and e.file_size > 512 * 1024 * 1024 for e in entries)):
                    raise ValueError('Workbook exceeds safe extraction limits')
            workbook_file = path.open('rb')
            workbook = load_workbook(workbook_file, read_only=True, data_only=True, keep_links=False)
        except ValueError:
            if workbook_file is not None:
                workbook_file.close()
            raise
        except Exception:
            if workbook_file is not None:
                workbook_file.close()
            raise ValueError('Unable to read this XLSX workbook') from None
        try:
            required = ('CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE')
            optional = ('EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE')
            fields = required + optional
            matches = []
            for sheet in workbook.worksheets:
                if progress:
                    progress('scanning', 0)
                max_row, max_column = _xlsx_dimensions(workbook, sheet, progress)
                if not max_row or not max_column:
                    continue
                sheet.reset_dimensions()
                header = next(sheet.iter_rows(min_row=1, max_row=1, max_col=max_column, values_only=True), ())
                positions = {field: [index for index, value in enumerate(header) if value == field] for field in fields}
                if all(len(positions[field]) == 1 for field in required):
                    if any(len(positions[field]) > 1 for field in fields):
                        raise ValueError('Workbook has duplicate LCA field headers')
                    matches.append((sheet, positions, max_row, max_column))
            if len(matches) != 1:
                raise ValueError('Workbook must contain exactly one LCA disclosure sheet with the required headers')
            sheet, positions, max_row, max_column = matches[0]
            if progress:
                progress('reading', 0)
            def lines():
                buffer = io.StringIO(newline='')
                writer = csv.writer(buffer)
                def line(values):
                    buffer.seek(0); buffer.truncate(0); writer.writerow(values); return buffer.getvalue()
                yield line(fields)
                for row in sheet.iter_rows(min_row=2, max_row=max_row, max_col=max_column, values_only=True):
                    values = [row[positions[field][0]] if positions[field] and positions[field][0] < len(row) else None for field in fields]
                    if all(value is None for value in values):
                        continue
                    yield line([value.isoformat(' ') if isinstance(value, datetime) else value.isoformat() if isinstance(value, date)
                                else '' if value is None else str(value) for value in values])
            return self.import_csv(lines(), source_url, captured_on, progress=progress)
        except ValueError:
            raise
        except Exception:
            raise ValueError('Unable to read this XLSX workbook; no records were imported') from None
        finally:
            workbook.close()
            workbook_file.close()

    def coverage(self):
        row = self.conn.execute('SELECT COUNT(*) AS records,MIN(fiscal_year) AS first_year,MAX(fiscal_year) AS last_year,MAX(captured_on) AS latest_capture FROM h1b_lca').fetchone()
        imports = self.conn.execute('SELECT source_url,captured_on,imported_at,selected_fields_sha256,first_decision,last_decision,counts_json FROM h1b_lca_imports ORDER BY id DESC LIMIT 10').fetchall()
        history = [{**{k: item[k] for k in item.keys() if k != 'counts_json'}, 'counts': json.loads(item['counts_json'])} for item in imports]
        return {**dict(row), 'verification': 'user_imported_not_independently_verified',
                'complete': False, 'source_url': DOL_URL, 'lca_imports': history, 'uscis_snapshots': self.uscis.coverage()}

    def company(self, name, settings=None):
        settings = settings or self.settings()
        rows = self.conn.execute('SELECT fiscal_year,case_status,COUNT(*) AS cases FROM h1b_lca WHERE employer_key=? AND fiscal_year>=? GROUP BY fiscal_year,case_status ORDER BY fiscal_year DESC,case_status',
                                 (employer_key(name), settings['min_year'])).fetchall()
        recent = self.conn.execute('SELECT case_number,employer,fiscal_year,decision_date,case_status,city,state,job_title,source_url,captured_on FROM h1b_lca WHERE employer_key=? ORDER BY decision_date DESC,case_number LIMIT 20', (employer_key(name),)).fetchall()
        return {'employer': name, 'match': 'exact_case_whitespace_only',
                'uscis': self.uscis.company(name, settings['min_year']),
                'counts': [dict(row) for row in rows], 'records': [dict(row) for row in recent],
                'record_limit': 20, 'recent_certified_cases': sum(r['cases'] for r in rows if r['case_status'] == 'certified')}

    def employer_link(self, job_id):
        row = self.conn.execute('SELECT revision,employer,source_note,changed_at FROM h1b_employer_links WHERE job_id=? ORDER BY revision DESC LIMIT 1', (job_id,)).fetchone()
        return dict(row) if row else None

    def link_employer(self, job, data):
        """Save an explicitly selected imported entity; never assert legal equivalence."""
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm that this employer name is a candidate-provided association')
        revision, name, note = data.get('expected_revision'), data.get('employer'), data.get('source_note')
        if type(revision) is not int or revision < 0:
            raise ValueError('Expected employer association revision must be a nonnegative integer')
        if not isinstance(name, str) or len(name) > 300 or '\x00' in name:
            raise ValueError('Employer name must be text of at most 300 characters')
        name = name.strip()
        note = text(note, 'Source note', 500)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            current = self.employer_link(job.id)
            if revision != (current['revision'] if current else 0):
                raise ValueError('Employer association changed; reload before editing')
            if name:
                key = employer_key(name)
                row = self.conn.execute('''SELECT MIN(employer) FROM (
                    SELECT employer FROM h1b_lca WHERE employer_key=? UNION ALL
                    SELECT employer FROM uscis_employers WHERE employer_key=?)''', (key, key)).fetchone()
                if not row or not row[0]:
                    raise ValueError('Choose the exact legal employer name from imported DOL or USCIS records')
                name = row[0]
            self.conn.execute('INSERT INTO h1b_employer_links(job_id,revision,employer,source_note) VALUES(?,?,?,?)',
                              (job.id, revision + 1, name, note))
        return self.job(job)

    def job(self, job, settings=None):
        from .posting_checks import target
        settings = settings or self.settings()
        association = self.employer_link(job.id)
        history = self.company(association['employer'] if association and association['employer'] else job.company, settings)
        policy = h1b_policy(job.description, settings['need'])
        return {**policy, 'id': job.id, 'company': job.company, 'title': job.title, 'url': job.url,
                'live_check': self.posting_checks.latest(job.id), 'live_check_supported': target(job.url) is not None,
                'location': job.location, 'history': history, 'employer_association': association, 'posting_freshness': 'not_revalidated',
                'eligibility': 'not_determined', 'need': settings['need']}

    def opportunities(self, query='', category='all', offset=0):
        """Browse saved job descriptions independently of the optional filing imports."""
        if not isinstance(query, str) or len(query) > 200 or '\x00' in query:
            raise ValueError('Search must be text of at most 200 characters')
        if category not in ('all', 'potential', 'review', 'excluded'):
            raise ValueError('Unknown sponsorship signal filter')
        if type(offset) is not int or offset < 0:
            raise ValueError('Use a nonnegative offset')
        settings = self.settings()
        key = query.strip().casefold()
        matches = []
        counts = {'potential': 0, 'review': 0, 'excluded': 0}
        for job in self.store.list_jobs(limit=-1):
            if key and key not in ' '.join((job.title, job.company, job.location)).casefold():
                continue
            policy = h1b_policy(job.description, settings['need'])
            group = 'potential' if policy['potential_match'] else 'excluded' if policy['policy'] == 'excluded' else 'review'
            counts[group] += 1
            if category == 'all' or category == group:
                matches.append(job)
        return {'jobs': [self.job(job, settings) for job in matches[offset:offset + 25]],
                'total': len(matches), 'counts': counts, 'offset': offset,
                'next_offset': offset + 25 if offset + 25 < len(matches) else None,
                'settings': settings}

    def search(self, query, offset=0):
        query = text(query, 'Company search', 200)
        if len(query) < 2 or type(offset) is not int or offset < 0:
            raise ValueError('Use at least two characters and a nonnegative offset')
        settings = self.settings()
        # instr treats SQL wildcard characters literally. Search suggestions never imply an entity match.
        key = employer_key(query)
        employers = 'WITH employers AS (SELECT employer_key,employer FROM h1b_lca UNION ALL SELECT employer_key,employer FROM uscis_employers) '
        total = self.conn.execute(employers + 'SELECT COUNT(DISTINCT employer_key) FROM employers WHERE instr(employer_key,?)>0', (key,)).fetchone()[0]
        companies = self.conn.execute(employers + 'SELECT MIN(employer) AS employer FROM employers WHERE instr(employer_key,?)>0 GROUP BY employer_key ORDER BY employer_key LIMIT 25 OFFSET ?', (key, offset)).fetchall()
        jobs = [self.job(job, settings) for job in self.store.list_jobs(limit=-1) if key in employer_key(job.company)]
        if not settings['include_unknown']:
            jobs = [job for job in jobs if job['policy'] != 'unknown']
        return {'companies': [self.company(row['employer'], settings) for row in companies],
                'company_total': total, 'offset': offset, 'next_offset': offset + 25 if offset + 25 < total else None,
                'jobs': jobs, 'settings': settings, 'coverage': self.coverage(),
                'warning': 'LCA and USCIS history are separate from current job policy. No records means unknown, not no sponsorship. Live checks are dated ATS observations, not hiring or sponsorship guarantees.'}
