"""Optional real Chromium coverage of H-1B configuration, import and research."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from test_h1b import lca_csv
from career_agent.models import Job
from career_agent.storage import Store


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class H1BBrowserTests(unittest.TestCase):
    def test_import_preferences_search_and_safe_rendering(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from unittest.mock import patch
        from test_uscis import hub_csv, URL

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Example Inc.', 'title': 'Transfer role',
                    'job_url': 'https://example.invalid/transfer', 'description': 'H-1B transfers only.'}),
                    Job.from_mapping({'company': 'Example Inc.', 'title': 'Unknown role',
                    'job_url': 'https://example.invalid/unknown', 'description': 'Python skills.'})])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-coverage')).to_contain_text('0 imported')
                        page.locator('#h1b-need').select_option('new')
                        page.locator('#h1b-min-year').fill('2024')
                        page.locator('#h1b-unknown').uncheck()
                        page.get_by_role('button', name='Save H-1B preferences').click()
                        expect(page.locator('#h1b-status')).to_contain_text('preferences saved')
                        page.get_by_text('Import public DOL LCA records', exact=True).click()
                        page.locator('#h1b-captured').fill('2025-06-01')
                        page.locator('#h1b-file').set_input_files({'name': 'fictional.csv', 'mimeType': 'text/csv',
                            'buffer': lca_csv([{'JOB_TITLE': '<script>throw new Error("unsafe")</script>'}]).encode()})
                        page.locator('#h1b-consent').check()
                        page.get_by_role('button', name='Import records locally', exact=True).click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 added')
                        page.get_by_text('USCIS employer decisions: refresh or import', exact=True).click()
                        page.locator('#uscis-source').fill(URL)
                        page.locator('#uscis-year').fill('2025')
                        page.locator('#uscis-as-of').fill('2025-09-30')
                        page.locator('#uscis-file').set_input_files({'name': 'uscis-fictional.csv', 'mimeType': 'text/csv',
                            'buffer': hub_csv([{'Employer': 'Example Inc.'}])})
                        page.locator('#uscis-consent').check()
                        page.get_by_role('button', name='Import official CSV locally').click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 employer groups')
                        with patch('career_agent.uscis.public_get', return_value=(403, b'', 'text/html')):
                            page.locator('#uscis-consent').check()
                            page.get_by_role('button', name='Refresh from USCIS').click()
                            expect(page.locator('#h1b-status')).to_contain_text('HTTP 403')
                        page.locator('#h1b-query').fill('Example')
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 stored jobs')
                        expect(page.locator('#h1b-results')).to_contain_text('does not meet a new-sponsorship preference')
                        expect(page.locator('#h1b-results')).to_contain_text('<script>throw new Error')
                        expect(page.locator('#h1b-results')).to_contain_text('initial approvals/denials: 10/1')
                        page.get_by_role('button', name='Check live ATS posting').click()
                        expect(page.locator('#h1b-results')).to_contain_text('Unsupported posting URL')
                        self.assertEqual(page.locator('#h1b-results script').count(), 0)
                        page.locator('#h1b-query').fill('Unmatched Company')
                        expect(page.locator('#h1b-results')).to_be_empty()
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-results')).to_contain_text('No employer records found')
                        page.reload()
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-need')).to_have_value('new')
                        expect(page.locator('#h1b-unknown')).not_to_be_checked()
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
