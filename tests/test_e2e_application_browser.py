"""A complete fictional local application-preparation journey; no external submission."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ApplicationJourneyTests(unittest.TestCase):
    def test_verified_facts_to_reviewed_application_survive_reload(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
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
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-profile').click()
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.locator('#career-targets').fill('Data Analyst')
                        page.locator('#career-skills').fill('Python')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.locator('#fact-statement').fill('Built a fictional Python analytics dashboard.')
                        page.locator('#fact-source').fill('Fictional private source note.')
                        page.get_by_role('button', name='Save unverified fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified')

                        page.locator('#nav-jobs').click()
                        page.locator('#show-add-job').click()
                        page.locator('#add-job-form [name="title"]').fill('Data Analyst')
                        page.locator('#add-job-form [name="company"]').fill('Fictional Analytics')
                        page.locator('#add-job-form [name="location"]').fill('Remote USA')
                        page.locator('#add-job-form [name="job_url"]').fill('https://example.invalid/jobs/fictional')
                        page.locator('#add-job-form [name="description"]').fill('Python data analysis. Sponsorship may be available.')
                        page.get_by_role('button', name='Save opportunity').click()
                        expect(page.locator('#job-list')).to_contain_text('Data Analyst')
                        page.locator('#job-list button').first.click()
                        page.get_by_role('button', name='Create application draft').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Application packet', timeout=20000)
                        expect(page.locator('#detail-panel')).not_to_contain_text('Fictional private source note')
                        page.get_by_text('Resume draft', exact=True).click()
                        expect(page.locator('#detail-panel')).to_contain_text('Built a fictional Python analytics dashboard.')
                        approve = page.get_by_role('button', name='Approve for manual submission')
                        expect(approve).to_be_disabled()
                        page.get_by_label('I have personally reviewed this draft and its claims.').check()
                        approve.click()
                        expect(page.locator('#detail-panel')).to_contain_text('approved for manual submission', timeout=20000)
                        page.reload()
                        page.locator('#job-list button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('approved for manual submission', timeout=20000)
                        expect(page.locator('#detail-panel')).to_contain_text('Already applied on employer site?')
                        expect(page.locator('#count-submitted')).to_have_text('0')
                        page.get_by_label('Employer confirmation reference').fill('fictional-receipt-123')
                        page.get_by_label('I personally submitted this application externally.').check()
                        page.get_by_role('button', name='Record confirmed submission').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Submission recorded from your attestation', timeout=20000)
                        expect(page.locator('#count-submitted')).to_have_text('1')
                        page.locator('#nav-tracker').click()
                        expect(page.locator('#tracker-screen')).to_contain_text('Data Analyst')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
