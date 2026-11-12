"""A consented fictional analysis works without saving a career profile."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class AISampleBrowserTests(unittest.TestCase):
    def test_fictional_sample_needs_consent_and_does_not_create_user_data(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        row = {'quote': 'Experience building Python data pipelines.', 'fact_ids': ['sample-fact'],
               'reason': 'Review <script>fictional</script> evidence.'}
        output = {'requirements': [row, row]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-ai').click()
                        expect(page.locator('#ai-sample-run')).to_be_enabled()
                        expect(page.locator('#ai-run')).to_be_disabled()
                        page.get_by_text('View fictional sample').click()
                        expect(page.locator('#ai-sample-input')).to_contain_text('Built a fictional Python data pipeline.')
                        page.locator('#ai-sample-run').click()
                        expect(page.locator('#ai-sample-status')).to_contain_text('confirm the fictional test')
                        complete.assert_not_called()
                        page.locator('#ai-sample-consent').check(); page.locator('#ai-sample-run').click()
                        expect(page.locator('#ai-sample-status')).to_contain_text('Fictional sample only')
                        expect(page.locator('#ai-sample-results')).to_contain_text('Experience building Python data pipelines.')
                        expect(page.locator('#ai-sample-results')).to_contain_text('<script>fictional</script>')
                        expect(page.locator('#ai-sample-results')).to_contain_text('response repeats an excerpt')
                        self.assertEqual(page.locator('#ai-sample-results script').count(), 0)
                        expect(page.locator('#ai-sample-consent')).not_to_be_checked()
                        self.assertEqual(complete.call_count, 1)
                        with Store(db) as store:
                            self.assertEqual(store.list_jobs(), [])
                            self.assertEqual(CandidateWorkspace(store).latest()['revision'], 0)
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
