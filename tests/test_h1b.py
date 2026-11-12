"""F04: fictional LCA imports, provenance, entity boundaries and posting policy."""
import csv
import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from datetime import date
from pathlib import Path

from career_agent.h1b import DOL_URL, H1BResearch, fiscal_year, validate_dol_download_url
from career_agent.models import Job
from career_agent.sponsorship import h1b_policy
from career_agent.storage import Store


def lca_csv(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE', 'EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE'])
    writer.writeheader()
    for index, changes in enumerate(rows):
        writer.writerow({'CASE_NUMBER': f'I-200-25123-{index:06d}', 'EMPLOYER_NAME': 'Example Inc.', 'VISA_CLASS': 'H-1B', 'CASE_STATUS': 'Certified', 'DECISION_DATE': '2025-04-15', 'EMPLOYER_CITY': 'Example City', 'EMPLOYER_STATE': 'NY', 'JOB_TITLE': 'Engineer', **changes})
    return stream.getvalue()


class PolicyTests(unittest.TestCase):
    def test_transfer_restriction_does_not_exclude_unspecified_new_sponsorship(self):
        for text in ('H-1B transfers are not supported.', 'No H-1B transfers.',
                     'We do not sponsor H-1B transfers.', 'We cannot accept H-1B transfers.'):
            for need, expected in [('new', 'conditional'), ('any', 'conditional'), ('transfer', 'excluded')]:
                with self.subTest(text=text, need=need):
                    result = h1b_policy(text, need)
                    self.assertEqual(result['policy'], expected)
                    self.assertEqual(result['quotes'], [text])
                    self.assertFalse(result['potential_match'])
        self.assertEqual(h1b_policy('No visa sponsorship. H-1B transfers are not supported.', 'new')['policy'], 'excluded')
        self.assertEqual(h1b_policy('No H-1B transfers; no visa sponsorship.', 'new')['policy'], 'excluded')

    def test_scope_restrictions_and_denied_transfer_do_not_imply_general_support(self):
        cases = {
            'H-1B transfers are supported for existing employees only.': 'conditional',
            'H-1B transfers only for existing employees.': 'conditional',
            'H-1B sponsorship is available for Canadian citizens only.': 'conditional',
            'H-1B sponsorship is available, but only for executives.': 'conditional',
            'We cannot guarantee H-1B sponsorship is available.': 'conditional',
            'H-1B transfers are not available, but visa sponsorship is available.': 'conflicting',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = h1b_policy(text)
                self.assertEqual(result['policy'], expected)
                self.assertFalse(result['potential_match'])
                self.assertIn(text, result['quotes'])
        self.assertEqual(h1b_policy('H-1B transfers only.')['policy'], 'transfer_only')

    def test_posting_edge_cases(self):
        examples = {
            'Visa sponsorship is available.': 'possible',
            'Visa sponsorship is available; remote work is not available.': 'possible',
            'Visa sponsorship is available, but remote work is not available.': 'possible',
            'We cannot offer remote work but we can sponsor.': 'possible',
            'H-1B sponsorship is available for new applicants. H-1B transfers are supported.': 'possible',
            'H-1B sponsorship is available for new applicants. H-1B transfers only.': 'conflicting',
            'H-1B transfers are supported.': 'transfer_supported',
            'H1B sponsorship is available.': 'possible',
            'No visa sponsorship.': 'excluded',
            'We cannot sponsor candidates.': 'excluded',
            'Applicants must not require visa sponsorship.': 'excluded',
            'Visa sponsorship is not currently available.': 'excluded',
            'We are not offering visa sponsorship.': 'excluded',
            'We can sponsor visas other than H-1B.': 'excluded',
            'We are not sponsoring H1B candidates.': 'excluded',
            'We are not able to provide sponsorship.': 'excluded',
            'Must be a U.S. citizen.': 'excluded',
            'U.S. citizens only.': 'excluded',
            'We can sponsor. No visa sponsorship for this position.': 'conflicting',
            'Visa sponsorship available, but not for this role.': 'conflicting',
            'H-1B transfers only.': 'transfer_only',
            'H-1B transfers accepted if already selected.': 'conditional',
            'We may offer visa sponsorship.': 'conditional',
            'Visa sponsorship is available if requirements are met.': 'conditional',
            'Will you require sponsorship now or in the future?': 'conditional',
            'No sponsorship required.': 'conditional',
            'No visa sponsorship restrictions.': 'conditional',
            'No visa sponsorship for this role, other teams may sponsor.': 'excluded',
            'H-1B1 sponsorship available.': 'conditional',
            'E-3 visa sponsorship available.': 'conditional',
            'Equal opportunity regardless of citizenship.': 'unknown',
            'Must be authorized to work in the US.': 'unknown',
            'Python engineer.': 'unknown',
            '': 'unknown',
        }
        for description, expected in examples.items():
            with self.subTest(description=description):
                self.assertEqual(h1b_policy(description)['policy'], expected)

    def test_transfer_preference_never_claims_new_sponsorship(self):
        self.assertFalse(h1b_policy('H-1B transfers only.', 'new')['potential_match'])
        self.assertTrue(h1b_policy('H-1B transfers only.', 'transfer')['potential_match'])
        self.assertEqual(h1b_policy('<p>No visa sponsorship.</p>')['policy'], 'excluded')
        self.assertEqual(h1b_policy('H‑1B transfers only.')['policy'], 'transfer_only')


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.addCleanup(self.store.connection.close)
        self.research = H1BResearch(self.store)
        self.research.configure({'need': 'any', 'min_year': 2024, 'include_unknown': True})

    def ingest(self, rows, captured='2025-06-01', url=DOL_URL):
        return self.research.import_csv(io.StringIO(lca_csv(rows)), url, captured)

    def test_dol_refresh_downloads_only_allowed_url_and_records_atomic_provenance(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        url = 'https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx'
        path = Path(self.tmp.name) / 'fictional.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000001', 'Example Inc.', 'H-1B', 'Certified', '2025-04-15'])
        book.save(path); book.close()
        raw = path.read_bytes()
        import hashlib
        def fake_download(source, hosts, destination, **kwargs):
            self.assertEqual(source, url)
            self.assertEqual(hosts, {'www.dol.gov', 'dol.gov'})
            Path(destination).write_bytes(raw)
            return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        with patch('career_agent.public_sources.public_download', side_effect=fake_download):
            self.assertEqual(self.research.refresh_dol(url)['added'], 1)
        entry = self.research.coverage()['lca_imports'][0]
        self.assertEqual(entry['transport'], 'downloaded_from_dol_https')
        self.assertEqual(entry['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry['bytes'], len(raw))
        for bad in ('https://evil.invalid/media/LCA_Disclosure_Data_FY2026_Q3.xlsx',
                    url + '?redirect=https://evil.invalid', url.replace('/media/', '/other/'),
                    url.replace('Q3.xlsx', 'Q5.xlsx'), url + '#fragment'):
            with self.subTest(url=bad), patch('career_agent.public_sources.public_download') as download:
                with self.assertRaises(ValueError):
                    self.research.refresh_dol(bad)
                download.assert_not_called()
        with patch('career_agent.public_sources.public_download', side_effect=ValueError('blocked')):
            with self.assertRaisesRegex(ValueError, 'blocked'):
                self.research.refresh_dol(url)
        self.assertEqual(self.research.coverage()['records'], 1)
        self.assertEqual(len(self.research.coverage()['lca_imports']), 1)

    def test_import_history_is_atomic_persistent_and_distinguishes_observed_dates(self):
        rows = [{}, {'CASE_STATUS': 'Denied', 'DECISION_DATE': '2025-05-01'},
                {'VISA_CLASS': 'E-3 Australian'}]
        self.ingest(rows)
        first = self.research.coverage()['lca_imports'][0]
        self.assertEqual((first['first_decision'], first['last_decision']), ('2025-04-15', '2025-05-01'))
        self.assertEqual(first['counts']['added'], 2)
        self.assertEqual(first['counts']['other_visas_skipped'], 1)
        self.assertEqual(len(first['selected_fields_sha256']), 64)
        self.ingest(rows)
        repeat = self.research.coverage()['lca_imports'][0]
        self.assertEqual(repeat['selected_fields_sha256'], first['selected_fields_sha256'])
        self.assertEqual(repeat['counts']['duplicates'], 2)
        with self.assertRaises(ValueError):
            self.ingest([{'CASE_NUMBER': 'I-200-25123-999999'}, {'CASE_NUMBER': 'invalid'}])
        with self.assertRaisesRegex(ValueError, 'no data rows'):
            self.ingest([])
        self.assertEqual(len(self.research.coverage()['lca_imports']), 2)
        self.assertEqual(self.research.coverage()['records'], 2)
        reopened = Store(Path(self.tmp.name) / 'test.sqlite3')
        try:
            self.assertEqual(H1BResearch(reopened).coverage()['lca_imports'][0], repeat)
        finally:
            reopened.connection.close()

    def test_job_employer_association_is_explicit_versioned_and_reversible(self):
        job = Job.from_mapping({'company': 'Fictional Brand', 'title': 'Engineer',
                                'job_url': 'https://example.invalid/job', 'description': 'No visa sponsorship.'})
        self.store.add_jobs([job])
        self.ingest([{'EMPLOYER_NAME': 'Fictional Legal LLC'}])
        self.assertEqual(self.research.job(job)['history']['recent_certified_cases'], 0)
        data = {'employer': ' fictional   legal llc ', 'source_note': 'Careers page checked',
                'expected_revision': 0, 'acknowledged': True}
        for bad in ({**data, 'acknowledged': False}, {**data, 'source_note': ' '},
                    {**data, 'employer': 'Unknown LLC'}, {**data, 'expected_revision': 1}):
            with self.assertRaises(ValueError):
                self.research.link_employer(job, bad)
        result = self.research.link_employer(job, data)
        self.assertEqual(result['history']['employer'], 'Fictional Legal LLC')
        self.assertEqual(result['history']['recent_certified_cases'], 1)
        self.assertEqual(result['policy'], 'excluded')
        self.assertEqual(result['employer_association']['revision'], 1)
        with self.assertRaisesRegex(ValueError, 'reload'):
            self.research.link_employer(job, data)
        reopened = Store(Path(self.tmp.name) / 'test.sqlite3')
        try:
            self.assertEqual(H1BResearch(reopened).job(job)['employer_association']['source_note'], 'Careers page checked')
        finally:
            reopened.connection.close()
        removed = self.research.link_employer(job, {**data, 'employer': '', 'expected_revision': 1})
        self.assertEqual(removed['history']['employer'], 'Fictional Brand')
        self.assertEqual(removed['history']['recent_certified_cases'], 0)
        self.assertEqual(removed['employer_association']['revision'], 2)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM h1b_employer_links').fetchone()[0], 2)

    def test_original_workbook_import_preserves_dates_and_rolls_back_invalid_rows(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'fictional.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE', 'JOB_TITLE'])
        sheet.append(['I-200-25123-000001', 'Fictional Employer', 'H-1B', 'Certified', date(2025, 4, 15), 'Engineer'])
        sheet.append(['I-200-25123-000002', 'Fictional Employer', 'H-1B', 'Denied', date(2025, 4, 16), 'Analyst'])
        book.save(path); book.close()
        progress = []
        result = self.research.import_xlsx(path, DOL_URL, '2025-06-01', progress=lambda phase, amount: progress.append((phase, amount)))
        self.assertEqual(result['added'], 2)
        self.assertIn(('scanning', 0), progress)
        self.assertIn(('reading', 0), progress)
        self.assertEqual(self.research.company('Fictional Employer')['recent_certified_cases'], 1)
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000003', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15'])
        sheet.append(['invalid', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15'])
        book.save(path); book.close()
        with self.assertRaises(ValueError):
            self.research.import_xlsx(path, DOL_URL, '2025-06-02')
        self.assertEqual(self.research.coverage()['records'], 2)

    def test_import_progress_counts_rows_without_committing_early(self):
        observed = []
        def progress(phase, count):
            observed.append((phase, count))
            with Store(Path(self.tmp.name) / 'test.sqlite3') as other:
                self.assertEqual(other.connection.execute('SELECT count(*) FROM h1b_lca').fetchone()[0], 0)
        rows = [{} for _ in range(2000)]
        self.research.import_csv(io.StringIO(lca_csv(rows)), DOL_URL, '2025-06-01', progress=progress)
        self.assertEqual(observed, [('reading', 2000)])
        self.assertEqual(self.research.coverage()['records'], 2000)

    def test_workbook_staging_allows_writes_and_applies_against_latest_state(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'staged.xlsx'
        book = Workbook(write_only=True); sheet = book.create_sheet()
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        for index in range(2000):
            sheet.append([f'I-200-25123-{index:06d}', 'Example Inc.', 'H-1B', 'Certified', '2025-04-15'])
        book.save(path); book.close()
        phases = []
        def progress(phase, amount):
            phases.append((phase, amount))
            if phase == 'reading' and amount == 2000:
                with Store(Path(self.tmp.name) / 'test.sqlite3') as other:
                    research = H1BResearch(other)
                    self.assertEqual(research.coverage()['records'], 0)
                    newer = lca_csv([{'CASE_STATUS': 'Denied'}])
                    self.assertEqual(research.import_csv(io.StringIO(newer), DOL_URL, '2025-06-02')['added'], 1)
        result = self.research.import_xlsx(path, DOL_URL, '2025-06-01', progress=progress)
        self.assertEqual(result['added'], 1999)
        self.assertEqual(result['older_skipped'], 1)
        self.assertEqual(self.research.coverage()['records'], 2000)
        self.assertIn(('applying', 0), phases)
        self.assertEqual(self.store.connection.execute("SELECT case_status FROM h1b_lca WHERE case_number='I-200-25123-000000'").fetchone()[0], 'denied')

    def test_workbook_conflict_during_apply_rolls_back_every_row(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'conflict.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000001', 'Example Inc.', 'H-1B', 'Certified', '2025-04-15'])
        sheet.append(['I-200-25123-000002', 'Example Inc.', 'H-1B', 'Certified', '2025-04-15'])
        book.save(path); book.close()
        def progress(phase, amount):
            if phase == 'applying' and amount == 0:
                with Store(Path(self.tmp.name) / 'test.sqlite3') as other:
                    research = H1BResearch(other)
                    self.assertEqual(research.coverage()['records'], 0)
                    research.import_csv(io.StringIO(lca_csv([{'CASE_NUMBER': 'I-200-25123-000002', 'CASE_STATUS': 'Denied'}])), DOL_URL, '2025-06-01')
        opened = []
        real_tempfile = tempfile.TemporaryFile
        def make_temporary(*args, **kwargs):
            result = real_tempfile(*args, **kwargs)
            opened.append(result)
            return result
        with patch('career_agent.h1b.tempfile.TemporaryFile', side_effect=make_temporary):
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                self.research.import_xlsx(path, DOL_URL, '2025-06-01', progress=progress)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)
        coverage = self.research.coverage()
        self.assertEqual(coverage['records'], 1)
        self.assertEqual(len(coverage['lca_imports']), 1)

    def test_workbook_reimport_preserves_csv_duplicate_and_provenance_semantics(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'same.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE', 'EMPLOYER_CITY', 'EMPLOYER_STATE', 'JOB_TITLE'])
        sheet.append(['I-200-25123-000000', 'Example Inc.', 'H-1B', 'Certified', '2025-04-15', 'Example City', 'NY', 'Engineer'])
        book.save(path); book.close()
        self.ingest([{}])
        original = self.research.coverage()['lca_imports'][0]['selected_fields_sha256']
        result = self.research.import_xlsx(path, DOL_URL, '2025-06-01')
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(result['added'], 0)
        self.assertEqual(self.research.coverage()['lca_imports'][0]['selected_fields_sha256'], original)

    def test_workbook_requires_one_unambiguous_disclosure_sheet(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'fictional.xlsx'
        book = Workbook(); header = ['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE']
        book.active.append(header); book.create_sheet('Another').append(header)
        book.save(path); book.close()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.research.import_xlsx(path, DOL_URL, '2025-06-01')
        self.assertEqual(self.research.coverage()['records'], 0)

        book = Workbook(); book.active.append(header + ['unused'] * 1020)
        book.save(path); book.close()
        with self.assertRaisesRegex(ValueError, 'too many columns'):
            self.research.import_xlsx(path, DOL_URL, '2025-06-01')

    def test_malformed_workbook_xml_fails_cleanly_without_import(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        import zipfile
        path = Path(self.tmp.name) / 'valid.xlsx'
        book = Workbook(); book.active.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        book.save(path); book.close()
        for entry in ('xl/workbook.xml', 'xl/worksheets/sheet1.xml'):
            bad = Path(self.tmp.name) / 'bad.xlsx'
            with zipfile.ZipFile(path) as source, zipfile.ZipFile(bad, 'w') as target:
                for name in source.namelist():
                    target.writestr(name, b'<broken>' if name == entry else source.read(name))
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'Unable to read this XLSX'):
                self.research.import_xlsx(bad, DOL_URL, '2025-06-01')
            self.assertEqual(self.research.coverage()['records'], 0)

    def test_cli_accepts_xlsx_without_creating_csv(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        from contextlib import redirect_stdout
        from career_agent.cli import main
        path = Path(self.tmp.name) / 'fictional.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000001', 'Fictional Employer', 'H-1B', 'Certified', date(2025, 4, 15)])
        book.save(path); book.close()
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['--db', str(Path(self.tmp.name) / 'cli.sqlite3'), 'h1b-import', str(path),
                         '--source-url', DOL_URL, '--captured-on', '2025-06-01'])
        self.assertEqual(code, 0)
        self.assertIn('"added": 1', output.getvalue())
        self.assertEqual(list(Path(self.tmp.name).glob('*.csv')), [])

    def test_workbook_dimensions_are_verified_against_actual_cells(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        path = Path(self.tmp.name) / 'fictional.xlsx'
        original = Path(self.tmp.name) / 'original.xlsx'
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000001', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15'])
        sheet.append(['I-200-25123-000002', 'Fictional Employer', 'H-1B', 'Denied', '2025-04-16'])
        book.save(original); book.close()
        def rewrite(transform):
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(path, 'w') as target:
                for entry in source.infolist():
                    data = source.read(entry.filename)
                    if entry.filename == 'xl/worksheets/sheet1.xml':
                        data = transform(data)
                    target.writestr(entry, data)
        for dimension in (b'<dimension ref="A1:A1"/>', b'', b'<dimension ref="A1:XFD999999999"/>'):
            rewrite(lambda data: data.replace(b'<dimension ref="A1:E3"/>', dimension))
            result = self.research.import_xlsx(path, DOL_URL, '2025-06-01')
            self.assertEqual(result['added'] + result['duplicates'], 2)
        before = self.research.coverage()['records']
        for mutate in (
            lambda data: data.replace(b'<row r="3"', b'<row r="1048577"'),
            lambda data: data.replace(b'r="E3"', b'r="AMK3"'),
        ):
            rewrite(mutate)
            with self.assertRaises(ValueError):
                self.research.import_xlsx(path, DOL_URL, '2025-06-02')
            self.assertEqual(self.research.coverage()['records'], before)

    def test_opportunities_need_no_filings_and_respect_need_and_pagination(self):
        descriptions = ['Visa sponsorship is available.', 'H-1B transfers only.',
                        'No visa sponsorship.', 'Python skills.']
        self.store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': f'Role {i}',
            'location': 'Austin, TX', 'job_url': f'https://example.invalid/{i}', 'description': description})
            for i, description in enumerate(descriptions)])
        result = self.research.opportunities('austin')
        self.assertEqual(result['counts'], {'potential': 2, 'review': 1, 'excluded': 1})
        self.assertEqual(result['total'], 4)
        self.assertTrue(all(job['history']['recent_certified_cases'] == 0 for job in result['jobs']))
        self.research.configure({'need': 'new', 'min_year': 2024, 'include_unknown': False})
        result = self.research.opportunities(category='review')
        self.assertEqual(result['counts'], {'potential': 1, 'review': 2, 'excluded': 1})
        self.assertEqual({job['policy'] for job in result['jobs']}, {'unknown', 'transfer_only'})
        self.store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': f'Extra {i}',
            'job_url': f'https://example.invalid/extra/{i}'}) for i in range(26)])
        first = self.research.opportunities(category='review')
        self.assertEqual(len(first['jobs']), 25)
        self.assertEqual(first['next_offset'], 25)
        second = self.research.opportunities(category='review', offset=25)
        self.assertEqual(len(second['jobs']), 3)
        self.assertIsNone(second['next_offset'])
        for arguments in ({'category': 'bogus'}, {'offset': -1}, {'query': '\x00'}):
            with self.assertRaises(ValueError):
                self.research.opportunities(**arguments)

    def test_h1b_only_separate_status_counts_exact_entity_and_literals(self):
        result = self.ingest([{}, {'CASE_STATUS': 'Denied'}, {'CASE_STATUS': 'Withdrawn'},
                              {'CASE_STATUS': 'Certified-Withdrawn'}, {'VISA_CLASS': 'H-1B1 Chile'},
                              {'VISA_CLASS': 'E-3 Australian'}, {'EMPLOYER_NAME': 'Example Subsidiary'}])
        self.assertEqual(result['added'], 5)
        self.assertEqual(result['other_visas_skipped'], 2)
        company = self.research.company(' EXAMPLE   INC. ')
        self.assertEqual(company['recent_certified_cases'], 1)
        self.assertEqual(len(company['counts']), 4)
        self.assertEqual(self.research.company('Example Inc')['counts'], [])
        self.assertEqual(self.research.search('Example')['company_total'], 2)
        self.assertEqual(self.research.search("%' OR 1=1 --")['company_total'], 0)
        self.assertFalse(self.research.coverage()['complete'])

    def test_dol_certified_withdrawn_spelling_with_spaces_is_supported(self):
        self.ingest([{'CASE_STATUS': 'Certified - Withdrawn'}])
        company = self.research.company('Example Inc.')
        self.assertEqual(company['counts'][0]['case_status'], 'certified-withdrawn')
        self.assertEqual(company['recent_certified_cases'], 0)
        with self.assertRaisesRegex(ValueError, 'unsupported CASE_STATUS'):
            self.ingest([{'CASE_STATUS': 'Certified - Pending'}], captured='2025-06-02')

    def test_deduplication_conflicts_and_older_decisions_are_atomic(self):
        self.ingest([{}])
        self.assertEqual(self.ingest([{}])['duplicates'], 1)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.ingest([{'CASE_NUMBER': 'I-200-25123-999999'}, {'CASE_NUMBER': 'I-200-25123-000000', 'CASE_STATUS': 'Denied'}])
        self.assertEqual(self.research.coverage()['records'], 1)
        self.assertEqual(self.ingest([{'CASE_STATUS': 'Certified-Withdrawn', 'DECISION_DATE': '2025-07-01'}], '2025-07-02')['updated'], 1)
        self.assertEqual(self.ingest([{}], '2025-08-01')['older_skipped'], 1)
        self.assertEqual(self.research.company('Example Inc.')['recent_certified_cases'], 0)
        self.assertEqual(self.ingest([{}])['older_skipped'], 1)

    def test_invalid_dates_statuses_sources_and_shapes(self):
        for change in ({'DECISION_DATE': '2027-01-01'}, {'CASE_STATUS': 'Pending'}, {'VISA_CLASS': 'unknown'}, {'EMPLOYER_NAME': ''}, {'CASE_NUMBER': 'private-case'}, {'DECISION_DATE': 'not a date'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ingest([change])
        for url in ('http://www.dol.gov/data', 'https://dol.gov.evil.invalid', 'https://www.dol.gov@evil.invalid', 'https://user:password@www.dol.gov'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.ingest([{}], url=url)
        with self.assertRaises(ValueError):
            self.ingest([{}], '2099-01-01')
        with self.assertRaises(ValueError):
            self.research.import_csv(io.StringIO('employer,status\nExample,Certified'), DOL_URL, '2025-06-01')
        self.assertEqual(self.research.coverage()['records'], 0)

    def test_fiscal_window_pagination_and_job_restrictions(self):
        self.assertEqual(fiscal_year(date(2025, 9, 30)), 2025)
        self.assertEqual(fiscal_year(date(2025, 10, 1)), 2026)
        self.ingest([{'EMPLOYER_NAME': f'Example {i:02d}'} for i in range(26)] + [{'EMPLOYER_NAME': 'Example Inc.'}])
        self.assertEqual(self.research.search('Example')['next_offset'], 25)
        self.assertEqual(len(self.research.search('Example', 25)['companies']), 2)
        job = Job.from_mapping({'company': 'Example Inc.', 'title': 'Role', 'job_url': 'https://example.invalid/job', 'description': 'No visa sponsorship.'})
        unknown = Job.from_mapping({'company': 'Example Inc.', 'title': 'Other', 'job_url': 'https://example.invalid/other'})
        self.store.add_jobs([job, unknown])
        info = self.research.job(job)
        self.assertFalse(info['live_check_supported'])
        supported = Job.from_mapping({'company': 'Example Inc.', 'title': 'ATS role', 'job_url': 'https://boards.greenhouse.io/example/jobs/123'})
        self.assertTrue(self.research.job(supported)['live_check_supported'])
        self.assertEqual(info['policy'], 'excluded')
        self.assertEqual(info['history']['recent_certified_cases'], 1)
        self.assertFalse(info['potential_match'])
        self.research.configure({'need': 'new', 'min_year': 2026, 'include_unknown': False})
        self.assertEqual(self.research.company('Example Inc.')['recent_certified_cases'], 0)
        self.assertEqual(len(self.research.search('Example')['jobs']), 1)
        for data in ({'need': 'new'}, {'need': 'wrong', 'min_year': 2025, 'include_unknown': True}, {'need': 'new', 'min_year': True, 'include_unknown': True}):
            with self.assertRaises(ValueError):
                self.research.configure(data)
