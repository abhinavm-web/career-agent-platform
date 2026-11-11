"""F04: fictional LCA imports, provenance, entity boundaries and posting policy."""
import csv
import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from career_agent.h1b import DOL_URL, H1BResearch, fiscal_year
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
        result = self.research.import_xlsx(path, DOL_URL, '2025-06-01')
        self.assertEqual(result['added'], 2)
        self.assertEqual(self.research.company('Fictional Employer')['recent_certified_cases'], 1)
        book = Workbook(); sheet = book.active
        sheet.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
        sheet.append(['I-200-25123-000003', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15'])
        sheet.append(['invalid', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15'])
        book.save(path); book.close()
        with self.assertRaises(ValueError):
            self.research.import_xlsx(path, DOL_URL, '2025-06-02')
        self.assertEqual(self.research.coverage()['records'], 2)

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
