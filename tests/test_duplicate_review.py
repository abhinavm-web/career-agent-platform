"""Possible-duplicate lookup only reads saved jobs; it never merges them."""
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from career_agent.models import Job
from career_agent.storage import Store


class DuplicateReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'fictional.sqlite3')
        self.addCleanup(self.store.connection.close)
        self.job = Job(company='Example Co', title='Data Analyst', location='Boston, MA',
                       url='https://example.invalid/jobs/1', source='indeed')
        self.store.add_jobs([self.job])

    def test_only_exact_company_title_and_nonempty_location_are_suggested(self):
        exact = replace(self.job, company=' EXAMPLE   CO ', title='data analyst', location='BOSTON, ma',
                        url='https://example.invalid/jobs/2', source='linkedin')
        different_title = replace(self.job, title='Senior Data Analyst', url='https://example.invalid/jobs/3')
        different_location = replace(self.job, location='Austin, TX', url='https://example.invalid/jobs/4')
        unknown_location = replace(self.job, location='', url='https://example.invalid/jobs/5')
        self.store.add_jobs([exact, different_title, different_location, unknown_location])
        self.store.stage(exact.id)
        self.store.confirm(exact.id, 'fictional-receipt', attested=True)
        self.store.record_review(self.job.id, 'a' * 64, approved=True)
        before = self.store.review_history()
        result = self.store.possible_duplicates(self.job.id)
        self.assertEqual([row['job_id'] for row in result['jobs']], [exact.id])
        self.assertTrue(result['jobs'][0]['submission_recorded'])
        self.assertEqual(result['jobs'][0]['source'], 'linkedin')
        self.assertIn('different requisitions', result['reason'])
        self.assertEqual(self.store.review_history(), before)
        self.assertEqual(len(self.store.list_jobs()), 5)
        self.assertEqual(self.store.possible_duplicates(unknown_location.id)['jobs'], [])
        self.assertIn('missing', self.store.possible_duplicates(unknown_location.id)['reason'])

    def test_result_limit_and_scan_limit_are_explicit(self):
        self.store.add_jobs([replace(self.job, url=f'https://example.invalid/jobs/dup-{i}') for i in range(12)])
        result = self.store.possible_duplicates(self.job.id)
        self.assertEqual(len(result['jobs']), 10)
        self.assertTrue(result['results_truncated'])
        self.assertFalse(result['scan_limited'])

        other = replace(self.job, title='Unique role', url='https://example.invalid/jobs/unique')
        self.store.add_jobs([other, replace(other, url='https://example.invalid/jobs/old-match')])
        with self.store.connection:
            self.store.connection.executemany(
                'INSERT INTO jobs(job_id,company,title,location,url,description,source,remote) VALUES(?,?,?,?,?,?,?,?)',
                [(f'x{i}', 'Other', f'Role {i}', 'Elsewhere', f'https://example.invalid/x/{i}', '', 'test', 0)
                 for i in range(10001)])
        result = self.store.possible_duplicates(other.id)
        self.assertEqual(result['jobs'], [])
        self.assertTrue(result['scan_limited'])
        self.assertFalse(result['results_truncated'])


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class DuplicateBrowserTests(unittest.TestCase):
    def test_compare_and_open_other_saved_record_without_mutation(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            first = Job(company='Fictional Co', title='Analyst', location='Boston',
                        url='https://example.invalid/one', source='first-board')
            second = replace(first, url='https://example.invalid/two', source='second-board')
            with Store(db) as store:
                store.add_jobs([first, second])
                store.stage(first.id); store.confirm(first.id, 'fictional-receipt', attested=True)
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page()
                        page.add_init_script("""const originalFetch = window.fetch;
                          window.fetch = async (...args) => {
                            if (String(args[0]).includes('/packet')) {
                              await new Promise(resolve => setTimeout(resolve, 1200));
                              try { return await originalFetch(...args); } finally { window.packetSettled = true; }
                            }
                            return originalFetch(...args);
                          };""")
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#job-list button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('second-board')
                        page.get_by_role('button', name='Find possible duplicates').click()
                        expect(page.locator('#detail-panel')).to_contain_text('first-board')
                        expect(page.locator('#detail-panel')).to_contain_text('Submission recorded')
                        page.wait_for_function('window.packetSettled === true')
                        expect(page.locator('#detail-panel')).to_contain_text('first-board')
                        page.get_by_role('button', name='Open other saved opportunity').click()
                        expect(page.locator('#detail-panel')).to_contain_text('first-board')
                    finally:
                        browser.close()
                with Store(db) as store:
                    self.assertEqual(len(store.list_jobs()), 2)
                    self.assertEqual(store.stage_of(first.id), 'submitted')
                    self.assertEqual(store.stage_of(second.id), 'discovered')
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
