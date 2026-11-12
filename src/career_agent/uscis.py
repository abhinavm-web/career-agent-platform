"""USCIS employer hub snapshots, kept separate from LCAs and current job policies."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from .h1b import fiscal_year, text
from .public_sources import public_get, SourceUnavailable
from .sponsor_evidence import employer_key

USCIS_HUB = 'https://www.uscis.gov/archive/h-1b-employer-data-hub-files'
COUNTS = ('Initial Approval', 'Initial Denial', 'Continuing Approval', 'Continuing Denial')
IDENTITY = ('Employer', 'Tax ID', 'NAICS', 'State', 'City', 'ZIP')


def source_url(value):
    value = text(value, 'USCIS CSV URL', 2000)
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.hostname not in ('www.uscis.gov', 'uscis.gov')
            or parts.username or parts.password or parts.port not in (None, 443)
            or not parts.path.startswith('/sites/default/files/') or not parts.path.lower().endswith('.csv')
            or parts.query or parts.fragment):
        raise ValueError('Use an original HTTPS uscis.gov CSV file URL without query parameters')
    if not re.fullmatch(r'h1b_datahubexport-20\d{2}\.csv', parts.path.rsplit('/', 1)[-1], flags=re.I):
        raise ValueError('Use a supported H-1B annual file named h1b_datahubexport-YYYY.csv; other USCIS programs/formats require a separate adapter')
    return value


def number(value):
    value = value.strip()
    if value in ('', '-', '*', 'D', '<3', '< 3'):
        return None  # Missing/suppressed is not zero.
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', value):
        raise ValueError('USCIS count has invalid numeric formatting')
    value = value.replace(',', '')
    if not value.isascii() or not value.isdigit() or len(value) > 9:
        raise ValueError('USCIS counts must be nonnegative integers or missing/suppressed markers')
    return int(value)


def validate_release(url, year, data_as_of):
    """Reject inconsistent release metadata before any network or database work."""
    url = source_url(url)
    if type(year) is not int or not 2009 <= year <= fiscal_year():
        raise ValueError('Select a valid USCIS fiscal year from 2009 through the current year')
    if not url.lower().endswith(f'-{year}.csv'):
        raise ValueError('USCIS source filename year does not match the selected fiscal year')
    try:
        as_of = date.fromisoformat(data_as_of)
    except (ValueError, TypeError):
        raise ValueError('Data as of must be YYYY-MM-DD') from None
    if as_of.isoformat() != data_as_of or not date(year - 1, 10, 1) <= as_of <= min(date(year, 9, 30), date.today()):
        raise ValueError('Data as of must be within the selected fiscal year and not in the future')
    return url


class USCISRecords:
    def __init__(self, store):
        self.conn = store.connection
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS uscis_snapshots (
                fiscal_year INTEGER PRIMARY KEY, data_as_of TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                source_url TEXT NOT NULL, transport TEXT NOT NULL, sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uscis_employers (
                fiscal_year INTEGER NOT NULL REFERENCES uscis_snapshots(fiscal_year),
                row_key TEXT NOT NULL, employer TEXT NOT NULL, employer_key TEXT NOT NULL,
                tax_suffix TEXT NOT NULL, naics TEXT NOT NULL, state TEXT NOT NULL, city TEXT NOT NULL, zip TEXT NOT NULL,
                initial_approval INTEGER, initial_denial INTEGER, continuing_approval INTEGER, continuing_denial INTEGER,
                PRIMARY KEY(fiscal_year,row_key)
            );
            CREATE INDEX IF NOT EXISTS uscis_name ON uscis_employers(employer_key,fiscal_year);
        ''')

    def import_bytes(self, content, url, year, data_as_of, transport='user_imported_not_independently_verified'):
        url = validate_release(url, year, data_as_of)
        if len(content) > 32 * 1024 * 1024:
            raise ValueError('USCIS CSV exceeds 32 MB')
        try:
            decoded = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('USCIS CSV must be UTF-8') from None
        reader = csv.DictReader(io.StringIO(decoded), strict=True)
        required = {'Fiscal Year', *IDENTITY, *COUNTS}
        if not required.issubset(reader.fieldnames or []) or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('Unrecognized USCIS employer CSV headers; no data was changed')
        rows = {}
        try:
            for line, row in enumerate(reader, 2):
                if None in row or any(row.get(key) is None for key in required):
                    raise ValueError(f'USCIS row {line}: malformed row')
                if row['Fiscal Year'].strip() != str(year):
                    raise ValueError(f'USCIS row {line}: fiscal year does not match selection')
                identity = [row[key].strip() for key in IDENTITY]
                if any(len(value) > 300 or '\x00' in value for value in identity):
                    raise ValueError(f'USCIS row {line}: invalid employer identity fields')
                employer, tax, naics, state, city, zip_code = identity
                if tax not in ('', '-') and (not 1 <= len(tax) <= 4 or not tax.isascii() or not tax.isdigit()):
                    raise ValueError(f'USCIS row {line}: expected the public four-digit Tax ID suffix, not a full identifier')
                # Keep public aggregation dimensions, not a guessed global corporate identity.
                key = hashlib.sha256(json.dumps([employer_key(v) for v in identity], ensure_ascii=False).encode()).hexdigest()
                values = (year, key, employer, employer_key(employer), tax, naics, state, city, zip_code,
                          *(number(row[k]) for k in COUNTS))
                if key in rows and rows[key] != values:
                    raise ValueError(f'USCIS row {line}: conflicting duplicate aggregation group')
                rows[key] = values
        except csv.Error as error:
            raise ValueError('Invalid USCIS CSV structure') from error
        if not rows:
            raise ValueError('Empty USCIS data cannot replace a saved snapshot')
        normalized = hashlib.sha256(json.dumps(sorted(rows.values()), ensure_ascii=False).encode()).hexdigest()
        raw_hash = hashlib.sha256(content).hexdigest()
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            old = self.conn.execute('SELECT * FROM uscis_snapshots WHERE fiscal_year=?', (year,)).fetchone()
            if old and old['data_as_of'] > data_as_of:
                raise ValueError('An older USCIS reporting period cannot replace the saved snapshot')
            if old and old['data_as_of'] == data_as_of and old['content_sha256'] != normalized:
                raise ValueError('Conflicting USCIS data for the same reporting date; inspect the sources before replacing')
            if old and old['data_as_of'] == data_as_of and old['content_sha256'] == normalized:
                return {'rows': old['row_count'], 'unchanged': True, 'fiscal_year': year}
            self.conn.execute('DELETE FROM uscis_employers WHERE fiscal_year=?', (year,))
            self.conn.execute('INSERT OR REPLACE INTO uscis_snapshots VALUES(?,?,?,?,?,?,?,?)',
                              (year, data_as_of, datetime.now(timezone.utc).isoformat(), url, transport, raw_hash, normalized, len(rows)))
            self.conn.executemany('INSERT INTO uscis_employers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', rows.values())
        return {'rows': len(rows), 'unchanged': False, 'fiscal_year': year}

    def refresh(self, url, year, data_as_of):
        url = validate_release(url, year, data_as_of)
        status, content, content_type = public_get(url, {'www.uscis.gov', 'uscis.gov'}, maximum=32 * 1024 * 1024)
        if status != 200:
            raise SourceUnavailable(f'USCIS returned HTTP {status}; saved records were kept. No USCIS API token is needed. Download the official complete annual CSV in your browser and import it locally if available.')
        if 'html' in content_type.lower() or content.lstrip().startswith(b'<'):
            raise SourceUnavailable('USCIS returned a page instead of CSV; saved records were kept')
        return self.import_bytes(content, url, year, data_as_of, 'downloaded_from_uscis_https')

    def coverage(self):
        return [{**dict(row), 'reporting_date_verification': 'user_supplied_release_date'}
                for row in self.conn.execute('SELECT * FROM uscis_snapshots ORDER BY fiscal_year DESC')]

    def company(self, name, min_year):
        rows = self.conn.execute('SELECT e.*,s.data_as_of,s.retrieved_at,s.source_url,s.transport,s.sha256 FROM uscis_employers e JOIN uscis_snapshots s USING(fiscal_year) WHERE employer_key=? AND fiscal_year>=? ORDER BY fiscal_year DESC,state,city,row_key', (employer_key(name), min_year)).fetchall()
        keys = ('initial_approval', 'initial_denial', 'continuing_approval', 'continuing_denial')
        totals = {key: sum(row[key] for row in rows) if rows and all(row[key] is not None for row in rows) else None for key in keys}
        snapshots = self.conn.execute('SELECT EXISTS(SELECT 1 FROM uscis_snapshots), EXISTS(SELECT 1 FROM uscis_snapshots WHERE fiscal_year>=?)', (min_year,)).fetchone()
        data_status = ('no_data' if not snapshots[0] else 'outside_window' if not snapshots[1] else
                       'no_exact_match' if not rows else 'in_window')
        return {'totals': totals, 'records': [dict(row) for row in rows], 'data_status': data_status, 'min_year': min_year,
                'warning': 'First-decision historical counts, not current vacancies or individual eligibility. Initial/continuing categories are not new-cap/transfer categories. Missing or suppressed counts remain unknown. Reporting cut-off dates are user-supplied from the release.'}
