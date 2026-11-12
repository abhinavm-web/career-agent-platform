"""Local H-1B LCA index. Imports are user-supplied, never independently verified."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import unicodedata
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


def employer_search_key(value):
    """Broader discovery only; never use this key to join filing records."""
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold() if char.isalnum())


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


def validate_dol_download_url(url):
    """Only explicitly named DOL quarterly disclosure workbooks are fetchable."""
    url, _ = validate_lca_source(url, date.today().isoformat())
    parts = urlsplit(url)
    if (parts.query or parts.fragment or not re.fullmatch(
            r'/media/LCA_Disclosure_Data_FY20\d{2}_Q[1-4]\.xlsx', parts.path)):
        raise ValueError('Use an official DOL quarterly workbook URL under /media/LCA_Disclosure_Data_FYyyyy_Qn.xlsx')
    return url


def _validate_lca_row(row, line, captured):
    """Validate one disclosure row independently of existing workspace data."""
    required = ('CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE')
    if None in row or any(row.get(key) is None for key in required):
        raise ValueError(f'Row {line}: malformed CSV row')
    visa = text(row['VISA_CLASS'], f'Row {line} VISA_CLASS').upper()
    if visa in ('E-3 AUSTRALIAN', 'H-1B1 CHILE', 'H-1B1 SINGAPORE'):
        return None
    if visa != 'H-1B':
        raise ValueError(f'Row {line}: unsupported VISA_CLASS')
    case = text(row['CASE_NUMBER'], f'Row {line} CASE_NUMBER', 80).upper()
    if not re.fullmatch(r'I-\d{3}-\d{5}-\d{6}', case):
        raise ValueError(f'Row {line}: expected a public DOL LCA CASE_NUMBER')
    employer = text(row['EMPLOYER_NAME'], f'Row {line} EMPLOYER_NAME')
    # DOL's FY2026 Q3 workbook uses "Certified - Withdrawn"; its layout omits spaces.
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
    optional = [text((row.get(k) or '').strip() or 'Unknown', f'Row {line} {k}') for k in ('EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE')]
    return case, employer, employer_key(employer), fiscal_year(decision), decision.isoformat(), status, *optional


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
            CREATE TABLE IF NOT EXISTS h1b_lca_acquisitions (
                import_id INTEGER PRIMARY KEY REFERENCES h1b_lca_imports(id),
                transport TEXT NOT NULL, raw_sha256 TEXT NOT NULL, bytes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS h1b_employer_links (
                job_id TEXT NOT NULL REFERENCES jobs(job_id), revision INTEGER NOT NULL,
                employer TEXT NOT NULL, source_note TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(job_id,revision)
            );
            CREATE TABLE IF NOT EXISTS h1b_company_links (
                company_key TEXT NOT NULL, revision INTEGER NOT NULL,
                company TEXT NOT NULL, employer TEXT NOT NULL, source_note TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(company_key,revision)
            );
            CREATE TABLE IF NOT EXISTS h1b_policy_notes (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                discussed_on TEXT NOT NULL, source TEXT NOT NULL, pathway TEXT NOT NULL,
                response TEXT NOT NULL, statement TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS h1b_policy_notes_job ON h1b_policy_notes(job_id,id DESC);
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

    def import_csv(self, stream, source_url, captured_on, *, progress=None, acquisition=None):
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
                validated = _validate_lca_row(row, line, captured)
                if validated is None:
                    counts['other_visas_skipped'] += 1
                    continue
                case = validated[0]
                decision = date.fromisoformat(validated[4])
                first_decision = min(first_decision, decision) if first_decision else decision
                last_decision = max(last_decision, decision) if last_decision else decision
                values = (*validated, source_url, captured_on, imported_at)
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
            imported = self.conn.execute('INSERT INTO h1b_lca_imports (source_url,captured_on,imported_at,selected_fields_sha256,first_decision,last_decision,counts_json) VALUES(?,?,?,?,?,?,?)',
                              (source_url, captured_on, imported_at, digest.hexdigest(),
                               first_decision.isoformat() if first_decision else None,
                               last_decision.isoformat() if last_decision else None, json.dumps(counts)))
            if acquisition is not None:
                self.conn.execute('INSERT INTO h1b_lca_acquisitions VALUES(?,?,?,?)',
                                  (imported.lastrowid, 'downloaded_from_dol_https', acquisition['sha256'], acquisition['bytes']))
        return counts

    def import_xlsx(self, path, source_url, captured_on, *, progress=None, acquisition=None):
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
        source_url, captured = validate_lca_source(source_url, captured_on)
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
            # Keep the long XLSX read and per-row validation outside the workspace
            # write transaction. Only the selected public columns are staged.
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8', newline='') as staging:
                def staged_lines():
                    for item in lines():
                        staging.write(item)
                        yield item
                reader = csv.DictReader(staged_lines(), strict=True)
                for row_number, row in enumerate(reader, 2):
                    _validate_lca_row(row, row_number, captured)
                    if progress and (row_number - 1) % 2000 == 0:
                        progress('reading', row_number - 1)
                staging.flush()
                staging.seek(0)
                if progress:
                    progress('applying', 0)
                def apply_progress(_phase, amount):
                    progress('applying', amount)
                # Revalidation against the current workspace preserves identical
                # duplicate, stale and conflict semantics under concurrent writes.
                return self.import_csv(staging, source_url, captured_on,
                                       progress=apply_progress if progress else None, acquisition=acquisition)
        except ValueError:
            raise
        except OSError:
            raise ValueError('Unable to stage or read this XLSX workbook; check available disk space') from None
        except Exception:
            raise ValueError('Unable to read this XLSX workbook; no records were imported') from None
        finally:
            workbook.close()
            workbook_file.close()

    def refresh_dol(self, url, *, progress=None):
        """Fetch a selected public DOL release; never infer freshness/completeness."""
        from .public_sources import public_download
        url = validate_dol_download_url(url)
        with tempfile.TemporaryDirectory(prefix='career-dol-download-') as directory:
            path = Path(directory) / 'disclosure.xlsx'
            acquisition = public_download(url, {'www.dol.gov', 'dol.gov'}, path,
                                          maximum=512 * 1024 * 1024, progress=progress)
            if progress:
                progress('opening', 0)
            return self.import_xlsx(path, url, date.today().isoformat(),
                                    progress=progress, acquisition=acquisition)

    def coverage(self):
        row = self.conn.execute('SELECT COUNT(*) AS records,MIN(fiscal_year) AS first_year,MAX(fiscal_year) AS last_year,MAX(captured_on) AS latest_capture FROM h1b_lca').fetchone()
        imports = self.conn.execute('''SELECT i.source_url,i.captured_on,i.imported_at,i.selected_fields_sha256,
            i.first_decision,i.last_decision,i.counts_json,a.transport,a.raw_sha256,a.bytes
            FROM h1b_lca_imports i LEFT JOIN h1b_lca_acquisitions a ON a.import_id=i.id
            ORDER BY i.id DESC LIMIT 10''').fetchall()
        history = [{**{k: item[k] for k in item.keys() if k != 'counts_json'}, 'counts': json.loads(item['counts_json'])} for item in imports]
        return {**dict(row), 'verification': 'user_imported_not_independently_verified',
                'complete': False, 'source_url': DOL_URL, 'lca_imports': history, 'uscis_snapshots': self.uscis.coverage()}

    def company(self, name, settings=None):
        settings = settings or self.settings()
        rows = self.conn.execute('SELECT fiscal_year,case_status,COUNT(*) AS cases FROM h1b_lca WHERE employer_key=? AND fiscal_year>=? GROUP BY fiscal_year,case_status ORDER BY fiscal_year DESC,case_status',
                                 (employer_key(name), settings['min_year'])).fetchall()
        recent = self.conn.execute('SELECT case_number,employer,fiscal_year,decision_date,case_status,city,state,job_title,source_url,captured_on FROM h1b_lca WHERE employer_key=? ORDER BY decision_date DESC,case_number LIMIT 20', (employer_key(name),)).fetchall()
        has_data = bool(self.conn.execute('SELECT EXISTS(SELECT 1 FROM h1b_lca)').fetchone()[0])
        data_status = ('no_data' if not has_data else 'no_exact_match' if not recent else
                       'outside_window' if not rows else 'in_window')
        return {'employer': name, 'match': 'exact_case_whitespace_only',
                'uscis': self.uscis.company(name, settings['min_year']),
                'counts': [dict(row) for row in rows], 'records': [dict(row) for row in recent],
                'record_limit': 20, 'min_year': settings['min_year'], 'lca_data_status': data_status,
                'recent_certified_cases': sum(r['cases'] for r in rows if r['case_status'] == 'certified')}

    def board_history(self, jobs):
        """Batch existence hints for saved jobs; never infer sponsorship policy."""
        if not jobs:
            return {}
        ids = [job.id for job in jobs]
        marks = ','.join('?' for _ in ids)
        links = self.conn.execute(f'''SELECT l.job_id,l.employer FROM h1b_employer_links l
            JOIN (SELECT job_id,MAX(revision) AS revision FROM h1b_employer_links
                  WHERE job_id IN ({marks}) GROUP BY job_id) latest
            ON latest.job_id=l.job_id AND latest.revision=l.revision''', ids).fetchall()
        associations = {row['job_id']: row['employer'] for row in links if row['employer']}
        employers = {job.id: associations.get(job.id, job.company) for job in jobs}
        keys = list({employer_key(name) for name in employers.values()})
        key_marks = ','.join('?' for _ in keys)
        dol = {row[0] for row in self.conn.execute(
            f'SELECT DISTINCT employer_key FROM h1b_lca WHERE employer_key IN ({key_marks})', keys)}
        uscis = {row[0] for row in self.conn.execute(
            f'SELECT DISTINCT employer_key FROM uscis_employers WHERE employer_key IN ({key_marks})', keys)}
        results = {}
        for job in jobs:
            name = employers[job.id]
            key = employer_key(name)
            sources = (['DOL LCA'] if key in dol else []) + (['USCIS decisions'] if key in uscis else [])
            if sources:
                results[job.id] = {'employer': name, 'sources': sources,
                                   'match': 'candidate_association' if job.id in associations else 'exact_name'}
        return results

    def employer_link(self, job_id):
        row = self.conn.execute('SELECT revision,employer,source_note,changed_at FROM h1b_employer_links WHERE job_id=? ORDER BY revision DESC LIMIT 1', (job_id,)).fetchone()
        return dict(row) if row else None

    def company_link(self, company):
        row = self.conn.execute('SELECT revision,employer,source_note,changed_at FROM h1b_company_links WHERE company_key=? ORDER BY revision DESC LIMIT 1', (employer_key(company),)).fetchone()
        return dict(row) if row else None

    def company_link_history(self, job):
        rows = self.conn.execute('SELECT revision,employer,source_note,changed_at FROM h1b_company_links WHERE company_key=? ORDER BY revision DESC LIMIT 30', (employer_key(job.company),)).fetchall()
        return {'company': job.company, 'association': dict(rows[0]) if rows else None,
                'history': [dict(row) for row in rows]}

    def link_company(self, job, data):
        """Save a user-reviewed lookup for this exact displayed company name."""
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm that this company association is candidate-provided and not independently verified')
        revision, name, note = data.get('expected_revision'), data.get('employer'), data.get('source_note')
        if type(revision) is not int or revision < 0:
            raise ValueError('Expected company association revision must be a nonnegative integer')
        if not isinstance(name, str) or len(name) > 300 or '\x00' in name:
            raise ValueError('Employer name must be text of at most 300 characters')
        name = name.strip()
        note = text(note, 'Source note', 500)
        company = text(job.company, 'Company', 300)
        key = employer_key(company)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            current = self.company_link(company)
            if revision != (current['revision'] if current else 0):
                raise ValueError('Company association changed; reload before editing')
            if name:
                row = self.conn.execute('''SELECT MIN(employer) FROM (
                    SELECT employer FROM h1b_lca WHERE employer_key=? UNION ALL
                    SELECT employer FROM uscis_employers WHERE employer_key=?)''', (employer_key(name), employer_key(name))).fetchone()
                if not row or not row[0]:
                    raise ValueError('Choose the exact legal employer name from imported DOL or USCIS records')
                name = row[0]
            self.conn.execute('INSERT INTO h1b_company_links(company_key,revision,company,employer,source_note) VALUES(?,?,?,?,?)',
                              (key, revision + 1, company, name, note))
        return self.company_link_history(job)

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

    def policy_notes(self, job):
        rows = self.conn.execute('''SELECT id,discussed_on,source,pathway,response,statement,recorded_at
            FROM h1b_policy_notes WHERE job_id=? ORDER BY id DESC LIMIT 50''', (job.id,)).fetchall()
        return [dict(row) for row in rows]

    def add_policy_note(self, job, data):
        fields = {'discussed_on', 'source', 'pathway', 'response', 'statement', 'acknowledged'}
        if not isinstance(data, dict) or set(data) != fields or data['acknowledged'] is not True:
            raise ValueError('Provide all fields and acknowledge this is a candidate-entered note')
        try:
            discussed = date.fromisoformat(data['discussed_on'])
            if discussed.isoformat() != data['discussed_on'] or discussed > date.today():
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('Discussion date must be YYYY-MM-DD and no later than today') from None
        source = text(data['source'], 'Source', 300)
        statement = text(data['statement'], 'Statement', 2000)
        if not isinstance(data['pathway'], str) or data['pathway'] not in ('new', 'transfer', 'unspecified'):
            raise ValueError('Choose new sponsorship, transfer, or unspecified')
        if not isinstance(data['response'], str) or data['response'] not in ('supports', 'does_not_support', 'conditional', 'unclear'):
            raise ValueError('Choose a response category')
        with self.conn:
            self.conn.execute('''INSERT INTO h1b_policy_notes(job_id,discussed_on,source,pathway,response,statement)
                VALUES(?,?,?,?,?,?)''', (job.id, discussed.isoformat(), source, data['pathway'], data['response'], statement))
        return self.policy_notes(job)

    def job(self, job, settings=None):
        from .posting_checks import target
        settings = settings or self.settings()
        association = self.employer_link(job.id)
        company_association = self.company_link(job.company)
        # A reusable association is only a suggestion. The same displayed name
        # may describe different legal entities, especially for staffing firms.
        employer = association['employer'] if association and association['employer'] else job.company
        scope = 'job' if association is not None else 'exact_name'
        history = self.company(employer, settings)
        policy = h1b_policy(job.description, settings['need'])
        return {**policy, 'id': job.id, 'company': job.company, 'title': job.title, 'url': job.url,
                'live_check': self.posting_checks.latest(job.id), 'live_check_supported': target(job.url) is not None,
                'location': job.location, 'history': history, 'employer_association': association,
                'company_association': company_association, 'association_scope': scope, 'posting_freshness': 'not_revalidated',
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

    def search_employers(self, query, offset=0, *, name_mode='literal', include_location=True):
        """Search imported filing names without scanning saved jobs."""
        query = text(query, 'Company search', 200)
        if len(query) < 2 or type(offset) is not int or not 0 <= offset <= 9223372036854775807:
            raise ValueError('Use at least two characters and a nonnegative supported offset')
        if name_mode not in ('literal', 'punctuation'):
            raise ValueError('Unknown employer name search mode')
        # instr treats SQL wildcard characters literally. Search suggestions never imply an entity match.
        key = employer_key(query)
        employers = 'WITH employers AS (SELECT employer_key,employer FROM h1b_lca UNION ALL SELECT employer_key,employer FROM uscis_employers) '
        if name_mode == 'punctuation':
            candidate_key = employer_search_key(query)
            if len(candidate_key) < 2:
                raise ValueError('Use at least two letters or digits for the broader employer search')
            self.conn.create_function('h1b_search_key', 1, employer_search_key, deterministic=True)
            # OFFSET 0 prevents SQLite from pushing the Python normalization
            # predicate into the filing scan. Normalize distinct legal-name keys,
            # not every individual case; never group by the broader search key.
            names = employers + ', names AS (SELECT employer_key,MIN(employer) AS employer FROM employers GROUP BY employer_key LIMIT -1 OFFSET 0) '
            total = self.conn.execute(names + 'SELECT COUNT(*) FROM names WHERE instr(h1b_search_key(employer_key),?)>0', (candidate_key,)).fetchone()[0]
            companies = self.conn.execute(names + 'SELECT employer FROM names WHERE instr(h1b_search_key(employer_key),?)>0 ORDER BY employer_key LIMIT 25 OFFSET ?', (candidate_key, offset)).fetchall()
        else:
            total = self.conn.execute(employers + 'SELECT COUNT(DISTINCT employer_key) FROM employers WHERE instr(employer_key,?)>0', (key,)).fetchone()[0]
            companies = self.conn.execute(employers + 'SELECT MIN(employer) AS employer FROM employers WHERE instr(employer_key,?)>0 GROUP BY employer_key ORDER BY employer_key LIMIT 25 OFFSET ?', (key, offset)).fetchall()
        # A single DOL filing location is a clue for human review, not proof of
        # identity. Do not build complete company histories for a name picker.
        suggestions = []
        for row in companies:
            example = (self.conn.execute('SELECT city,state FROM h1b_lca WHERE employer_key=? ORDER BY decision_date DESC,case_number LIMIT 1',
                                         (employer_key(row['employer']),)).fetchone() if include_location else None)
            suggestions.append({'employer': row['employer'], 'records': [dict(example)] if example else []})
        return {'companies': suggestions, 'company_total': total, 'offset': offset,
                'next_offset': offset + 25 if offset + 25 < total else None, 'name_mode': name_mode}

    def search(self, query, offset=0, *, name_mode='literal'):
        result = self.search_employers(query, offset, name_mode=name_mode, include_location=False)
        settings = self.settings()
        result['companies'] = [self.company(item['employer'], settings) for item in result['companies']]
        key = employer_key(query.strip())
        candidate_key = employer_search_key(query.strip()) if name_mode == 'punctuation' else None
        jobs = [self.job(job, settings) for job in self.store.list_jobs(limit=-1)
                if (candidate_key in employer_search_key(job.company) if name_mode == 'punctuation'
                    else key in employer_key(job.company))]
        if not settings['include_unknown']:
            jobs = [job for job in jobs if job['policy'] != 'unknown']
        return {**result, 'jobs': jobs, 'settings': settings, 'coverage': self.coverage(),
                'warning': 'LCA and USCIS history are separate from current job policy. No records means unknown, not no sponsorship. Live checks are dated ATS observations, not hiring or sponsorship guarantees.'}
