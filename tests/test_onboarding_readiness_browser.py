"""A named profile without matching preferences should not appear fully set up."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class OnboardingReadinessBrowserTests(unittest.TestCase):
    def test_job_guides_empty_workspace_through_explicit_verification_and_drafting(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                                    'job_url': 'https://example.invalid/job', 'description': 'Python experience.'})
            with Store(db) as store: store.add_jobs([job])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        detail = page.locator('#detail-panel')
                        expect(detail).to_contain_text('a saved career profile and at least one verified career fact')
                        page.get_by_role('tab', name='Application').click()
                        expect(detail.get_by_role('button', name='Create application draft')).to_be_disabled()
                        expect(detail.get_by_text('Choose facts for a tailored draft')).to_have_count(0)
                        detail.get_by_role('button', name='Open career profile for draft setup').click()
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.locator('#fact-statement').fill('Built a fictional Python dashboard.')
                        page.locator('#fact-keywords').fill('Python')
                        page.locator('#fact-source').fill('Private fictional evidence')
                        page.get_by_role('button', name='Save unverified fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        page.get_by_role('button', name='Opportunity board', exact=True).click()
                        expect(detail).to_contain_text('at least one verified career fact')
                        page.get_by_role('tab', name='Application').click()
                        expect(detail.get_by_role('button', name='Create application draft')).to_be_disabled()
                        detail.get_by_role('button', name='Open career profile for draft setup').click()
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified')
                        page.get_by_role('button', name='Opportunity board', exact=True).click()
                        page.get_by_role('tab', name='Application').click()
                        expect(detail.get_by_role('button', name='Create application draft')).to_be_enabled()
                        page.get_by_role('tab', name='Application').click()
                        detail.get_by_role('button', name='Create application draft').click()
                        expect(page.locator('.review-card')).to_contain_text('Built a fictional Python dashboard.', timeout=20000)
                        expect(page.locator('.review-card')).not_to_contain_text('Private fictional evidence')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_setup_tracks_actual_matching_preferences(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from career_agent.models import CandidateProfile
        from career_agent.candidate import FactBank
        from test_dashboard import fake_fact_bank

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            with Store(db) as store:
                CandidateWorkspace(store).import_legacy(CandidateProfile((), (), (), True), FactBank.parse(fake_fact_bank()))
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
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#profile-prompt')).to_be_visible()
                        expect(page.locator('#setup-progress')).to_contain_text('Profile name saved')
                        expect(page.locator('#setup-progress')).to_contain_text('Add target roles')
                        expect(page.locator('#setup-progress')).to_contain_text('Add skills')
                        expect(page.locator('#setup-progress')).to_contain_text('Career facts verified')
                        page.get_by_role('button', name='Continue profile setup').click()
                        page.locator('#career-targets').fill('Data Analyst')
                        page.locator('#career-skills').fill('Python')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.get_by_role('button', name='Opportunity board', exact=True).click()
                        expect(page.locator('#profile-prompt')).to_be_visible()
                        expect(page.locator('#configuration-status')).to_have_text('Candidate profile configured')
                        page.get_by_role('button', name='Search job boards', exact=True).click()
                        expect(page.locator('#discovery-panel')).to_have_attribute('open', '')
                        expect(page.locator('#discover-consent')).not_to_be_checked()
                        page.get_by_role('button', name='Add a job manually').click()
                        form = page.locator('#add-job-form')
                        form.locator('[name="title"]').fill('Fictional Analyst')
                        form.locator('[name="company"]').fill('Fictional Company')
                        form.locator('[name="job_url"]').fill('https://example.invalid/onboarding')
                        form.get_by_role('button', name='Save opportunity').click()
                        expect(page.locator('#job-list')).to_contain_text('Fictional Analyst')
                        expect(page.locator('#profile-prompt')).to_be_hidden()
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
