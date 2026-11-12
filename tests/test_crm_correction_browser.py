"""Real-browser test for explicit, local terminal-outcome correction."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class CRMCorrectionBrowserTests(unittest.TestCase):
    def test_correct_a_mistaken_outcome_and_preserve_audit(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.crm import ApplicationCRM
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'title': 'Fictional Role', 'company': 'Example Co',
                'job_url': 'https://example.invalid/role', 'description': 'Fictional job.'})
            with Store(db) as store:
                store.add_jobs([job]); store.stage(job.id); store.confirm(job.id, 'fictional receipt', attested=True)
                crm = ApplicationCRM(store); crm.change_stage(job.id, 'interview'); crm.change_stage(job.id, 'rejected')
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
                        page.add_init_script('''
                            const realFetch = window.fetch.bind(window);
                            window.fetch = (url, options) => {
                              if (String(url) === '/api/overview' && !window.overviewHeld) {
                                window.overviewHeld = true;
                                return new Promise((resolve, reject) => {
                                  window.releaseOverview = () => realFetch(url, options).then(resolve, reject);
                                });
                              }
                              return realFetch(url, options);
                            };
                        ''')
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-tracker').click()
                        page.locator('#crm-jobs button').first.click()
                        detail = page.locator('#crm-detail')
                        expect(detail).to_contain_text('restore interview')
                        page.get_by_label('Reason for correction (private)').fill('Selected outcome by mistake')
                        page.wait_for_function('typeof window.releaseOverview === "function"')
                        page.evaluate('window.releaseOverview()')
                        expect(page.locator('#count-jobs')).to_have_text('1')
                        page.wait_for_timeout(300)
                        expect(page.get_by_label('Reason for correction (private)')).to_have_value('Selected outcome by mistake')
                        page.get_by_label('I confirm this outcome was recorded in error.').check()
                        page.get_by_role('button', name='Correct mistaken outcome').click()
                        expect(detail).to_contain_text('Correction of event')
                        expect(detail).to_contain_text('Corrected by event')
                        expect(detail).to_contain_text('Selected outcome by mistake')
                        page.reload(); page.locator('#nav-tracker').click()
                        page.locator('#crm-jobs button').first.click()
                        expect(detail).to_contain_text('Correction of event')
                        expect(detail).to_contain_text('Submission recorded from your independent attestation')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
