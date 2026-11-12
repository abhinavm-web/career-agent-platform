"""Compare fictional verified evidence with original Markdown section headings."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class FitHeadingsBrowserTests(unittest.TestCase):
    def test_generic_qualifications_are_visible_without_inventing_required_status(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.candidate import FactBank
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import CandidateProfile, Job
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from test_dashboard import fake_fact_bank
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'title': 'Engineer', 'company': 'Fictional Co',
                'job_url': 'https://example.invalid/engineer', 'description': '**Qualifications**\n- Python experience\n  is helpful but not required.\n**About You**\n- Python collaboration\n**Preferred** :\n- SQL experience'})
            with Store(db) as store:
                store.add_jobs([job])
                CandidateWorkspace(store).import_legacy(CandidateProfile(('Engineer',), ('Python',), (), True), FactBank.parse(fake_fact_bank()))
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
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        evidence = page.locator('#fit-evidence')
                        evidence.get_by_role('button', name='Compare requirements').click()
                        expect(evidence).to_contain_text('Qualification level unspecified')
                        expect(evidence).to_contain_text('Section heading: **Qualifications**')
                        expect(evidence).to_contain_text('- Python experience')
                        expect(evidence).to_contain_text('is helpful but not required.')
                        expect(evidence).to_contain_text('Potentially relevant verified fact experience-01')
                        expect(evidence).to_contain_text('Candidate-related section; level unspecified')
                        expect(evidence).to_contain_text('Section heading: **About You**')
                        expect(evidence).to_contain_text('Labeled preferred')
                        expect(evidence).not_to_contain_text('Labeled required')
                        preparation = page.locator('#interview-preparation')
                        preparation.get_by_role('button', name='Build preparation guide').click()
                        expect(preparation).to_contain_text('not actual employer interview questions')
                        expect(preparation).to_contain_text('Posting section: **Qualifications**')
                        expect(preparation).to_contain_text('- Python experience')
                        expect(preparation).to_contain_text('is helpful but not required.')
                        expect(preparation).to_contain_text('Possible example to review · experience-01')
                        expect(preparation).to_contain_text('Posting section: **About You**')
                        expect(preparation).not_to_contain_text('Private fictional evidence')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
