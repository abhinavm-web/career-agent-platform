"""Stage fictional résumé statements without verifying or overwriting unsaved edits."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class BulkResumeBrowserTests(unittest.TestCase):
    def test_bulk_staging_preserves_edits_and_requires_individual_verification(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
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
                        page.locator('#nav-profile').click()
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.locator('#career-headline').fill('Unsaved fictional headline')
                        page.locator('#fact-statement').fill('Unsaved separate fictional statement')
                        page.get_by_text('Import and review résumé text', exact=True).click()
                        page.locator('#resume-text').fill('EXPERIENCE\nBuilt a fictional Python tool.\nSUMMARY\nLed a fictional project.')
                        page.get_by_role('button', name='Find fact suggestions').click()
                        page.get_by_text('Stage several statements for review', exact=True).click()
                        page.locator('#resume-bulk-lines').select_option(['0', '1'])
                        page.get_by_role('button', name='Review selected lines as one fact').click()
                        expect(page.locator('#resume-bulk-status')).to_contain_text('Choose a category')
                        page.locator('#resume-bulk-category').select_option('projects')
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='Review selected lines as one fact').click()
                        expect(page.locator('#fact-statement')).to_have_value('Unsaved separate fictional statement')
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Review selected lines as one fact').click()
                        expect(page.locator('#fact-statement')).to_have_value('Built a fictional Python tool. Led a fictional project.')
                        expect(page.locator('#fact-category')).to_have_value('projects')
                        expect(page.locator('#career-facts-list')).to_contain_text('No career facts yet')
                        page.locator('#fact-statement').fill('Unsaved separate fictional statement')
                        page.locator('#resume-bulk-category').select_option('')
                        page.locator('#resume-bulk-source').fill('Check fictional project records.')
                        page.get_by_role('button', name='Stage selected statements').click()
                        expect(page.locator('#resume-bulk-status')).to_contain_text('Choose a category')
                        page.locator('#resume-bulk-category').select_option('projects')
                        page.get_by_role('button', name='Stage selected statements').click()
                        expect(page.locator('#resume-bulk-status')).to_contain_text('2 statements saved as unverified')
                        expect(page.locator('#career-headline')).to_have_value('Unsaved fictional headline')
                        expect(page.locator('#fact-statement')).to_have_value('Unsaved separate fictional statement')
                        with Store(db) as store:
                            workspace = CandidateWorkspace(store).latest()
                            self.assertEqual([row['section'] for row in workspace['facts']], ['experience', 'projects'])
                            self.assertTrue(all(not row['verified_by_candidate'] for row in workspace['facts']))
                            self.assertTrue(all(row['source_note'] == 'Check fictional project records.' for row in workspace['facts']))
                        page.locator('#resume-bulk-lines').select_option('0')
                        page.get_by_role('button', name='Stage selected statements').click()
                        expect(page.locator('#resume-bulk-status')).to_contain_text('already exists')
                        page.locator('#resume-text').fill('Changed fictional résumé text.')
                        expect(page.locator('#resume-bulk-save')).to_be_disabled()
                        expect(page.locator('#resume-combine')).to_be_disabled()
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').first.click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified', timeout=15000)
                        with Store(db) as store:
                            states = [row['verified_by_candidate'] for row in CandidateWorkspace(store).latest()['facts']]
                            self.assertEqual(sum(states), 1)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
