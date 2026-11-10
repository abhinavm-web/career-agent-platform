"""Fictional USCIS aggregates and fail-closed public-source reads."""
import csv
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from career_agent.h1b import H1BResearch
from career_agent.storage import Store
from career_agent.uscis import USCISRecords
from career_agent.public_sources import public_get, SourceUnavailable

URL = 'https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2025.csv'


def hub_csv(changes=None):
    rows = changes if changes is not None else [{}]
    fields = ['Fiscal Year', 'Employer', 'Initial Approval', 'Initial Denial', 'Continuing Approval', 'Continuing Denial', 'NAICS', 'Tax ID', 'State', 'City', 'ZIP']
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for change in rows:
        writer.writerow({'Fiscal Year': '2025', 'Employer': 'Fictional Corp', 'Initial Approval': '10', 'Initial Denial': '1', 'Continuing Approval': '20', 'Continuing Denial': '0', 'NAICS': '54', 'Tax ID': '0123', 'State': 'NY', 'City': 'Example', 'ZIP': '00000', **change})
    return buffer.getvalue().encode('utf-8')


class USCISTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'test.db')
        self.addCleanup(self.store.connection.close)
        self.records = USCISRecords(self.store)

    def test_snapshot_replacement_not_addition_and_entity_groups(self):
        first = hub_csv([{}, {'City': 'Other', 'Initial Approval': '5'}, {}])
        self.assertEqual(self.records.import_bytes(first, URL, 2025, '2025-03-31')['rows'], 2)
        self.assertEqual(self.records.company('fictional corp', 2025)['totals']['initial_approval'], 15)
        newer = hub_csv([{'Initial Approval': '18'}])
        self.records.import_bytes(newer, URL, 2025, '2025-06-30')
        self.assertEqual(self.records.company('Fictional Corp', 2025)['totals']['initial_approval'], 18)
        self.assertTrue(self.records.import_bytes(newer, URL, 2025, '2025-06-30')['unchanged'])
        with self.assertRaisesRegex(ValueError, 'older'):
            self.records.import_bytes(first, URL, 2025, '2025-03-31')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.records.import_bytes(first, URL, 2025, '2025-06-30')
        self.assertEqual(self.records.company('Fictional Corp Subsidiary', 2025)['records'], [])
        research = H1BResearch(self.store)
        research.configure({'need': 'new', 'min_year': 2025, 'include_unknown': True})
        result = research.search('Fictional')
        self.assertEqual(result['company_total'], 1)
        self.assertEqual(result['companies'][0]['recent_certified_cases'], 0)
        self.assertEqual(result['companies'][0]['uscis']['totals']['initial_approval'], 18)

    def test_invalid_and_suppressed_values_never_become_zero(self):
        self.records.import_bytes(hub_csv([{'Initial Approval': '*', 'City': ''}]), URL, 2025, '2025-09-30')
        self.assertIsNone(self.records.company('Fictional Corp', 2025)['totals']['initial_approval'])
        for changes in ([{'Tax ID': '123456789'}], [{'Initial Approval': '-2'}], [{'Initial Approval': '12,34'}], [{'Fiscal Year': '2024'}], [{}, {'Initial Approval': '99'}]):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.records.import_bytes(hub_csv(changes), URL, 2025, '2025-09-30')
        with self.assertRaises(ValueError):
            self.records.import_bytes(hub_csv([]), URL, 2025, '2025-09-30')
        with self.assertRaises(ValueError):
            self.records.import_bytes(hub_csv(), URL, 2025, '2026-01-01')
        with self.assertRaises(ValueError):
            self.records.import_bytes(hub_csv(), URL.replace('h1b_', 'h2b_'), 2025, '2025-09-30')
        with self.assertRaises(ValueError):
            self.records.import_bytes(hub_csv(), URL.replace('2025', '2024'), 2025, '2025-09-30')
        self.assertEqual(self.records.coverage()[0]['row_count'], 1)

    def test_failed_refresh_keeps_snapshot_and_source_metadata(self):
        self.records.import_bytes(hub_csv(), URL, 2025, '2025-03-31')
        for status, content, kind in [(403, b'', 'text/html'), (302, b'', ''), (200, b'<html>blocked</html>', 'text/html'), (200, b'invalid', 'text/csv')]:
            with self.subTest(status=status, content=content), patch('career_agent.uscis.public_get', return_value=(status, content, kind)):
                with self.assertRaises(ValueError):
                    self.records.refresh(URL, 2025, '2025-06-30')
            self.assertEqual(self.records.coverage()[0]['data_as_of'], '2025-03-31')
        with patch('career_agent.uscis.public_get', return_value=(200, hub_csv(), 'text/csv')):
            self.records.refresh(URL, 2025, '2025-06-30')
        snapshot = self.records.coverage()[0]
        self.assertEqual(snapshot['transport'], 'downloaded_from_uscis_https')
        self.assertEqual(len(snapshot['sha256']), 64)
        for url in ('http://www.uscis.gov/data.csv', 'https://uscis.gov.evil.invalid/file.csv', 'https://user@www.uscis.gov/sites/default/files/a.csv'):
            with self.assertRaises(ValueError), patch('career_agent.uscis.public_get') as fetch:
                self.records.refresh(url, 2025, '2025-06-30')
            fetch.assert_not_called()


class PublicSourceTests(unittest.TestCase):
    def test_rejects_private_dns_and_arbitrary_hosts_without_connecting(self):
        with patch('career_agent.public_sources._PinnedHTTPS') as connection:
            for url in ('https://127.0.0.1/data', 'https://www.uscis.gov.evil.invalid/data', 'http://www.uscis.gov/data', 'https://user:pass@www.uscis.gov/data'):
                with self.assertRaises(ValueError):
                    public_get(url, {'www.uscis.gov'})
            with patch('career_agent.public_sources.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]):
                with self.assertRaises(SourceUnavailable):
                    public_get(URL, {'www.uscis.gov'})
            connection.assert_not_called()

    def test_no_redirects_no_credentials_and_size_limits(self):
        dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch('career_agent.public_sources.socket.getaddrinfo', return_value=dns), patch('career_agent.public_sources._PinnedHTTPS') as constructor:
            conn = constructor.return_value
            response = conn.getresponse.return_value
            response.status = 302
            response.getheader.return_value = 'text/html'
            self.assertEqual(public_get(URL, {'www.uscis.gov'})[0], 302)
            self.assertEqual(conn.request.call_count, 1)
            headers = conn.request.call_args.kwargs['headers']
            self.assertNotIn('Cookie', headers)
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('X-Session-Token', headers)
            response.status = 200
            response.getheader.side_effect = lambda key, default=None: '5' if key == 'Content-Length' else 'text/csv'
            with self.assertRaises(SourceUnavailable):
                public_get(URL, {'www.uscis.gov'}, maximum=4)
            response.getheader.side_effect = lambda key, default=None: None if key == 'Content-Length' else 'text/csv'
            response.read.side_effect = [b'12345']
            with self.assertRaises(SourceUnavailable):
                public_get(URL, {'www.uscis.gov'}, maximum=4)
            self.assertTrue(conn.close.called)
