"""Fictional browser journey for consented, unverified résumé excerpt suggestions."""
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
class ResumeAIBrowserTests(unittest.TestCase):
    def test_consent_stale_text_and_separate_verification(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        excerpt = 'Built a fictional Python dashboard.'
        output = json.dumps({'suggestions': [{'quote': excerpt, 'section': 'projects'}]})
        ready, release = threading.Event(), threading.Event()
        calls = []
        def complete(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                ready.set(); release.wait(10)
            return output
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', side_effect=complete), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-profile').click()
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.get_by_text('Import and review résumé text').click()
                        page.locator('#resume-text').fill(excerpt)
                        page.get_by_text('Suggest structured facts with AI').click()
                        page.locator('#resume-ai-load').click()
                        expect(page.locator('#resume-ai-destination')).to_contain_text('ollama')
                        page.locator('#resume-ai-submit').click()
                        self.assertEqual(calls, [])
                        route['local']['base_url'] = 'http://127.0.0.1:1234/v1'
                        page.locator('#resume-ai-consent').check(); page.locator('#resume-ai-submit').click()
                        expect(page.locator('#resume-ai-status')).to_contain_text('reload routes and consent again')
                        self.assertEqual(calls, [])
                        route['local']['base_url'] = 'http://127.0.0.1:11434/v1'
                        page.locator('#resume-ai-consent').check(); page.locator('#resume-ai-submit').click()
                        self.assertTrue(ready.wait(5))
                        page.locator('#resume-text').fill(excerpt + ' Extra text.')
                        release.set()
                        expect(page.locator('#resume-ai-status')).to_contain_text('suggestions were discarded')
                        expect(page.locator('#resume-ai-results')).to_be_empty()
                        page.locator('#resume-text').fill(excerpt)
                        page.locator('#resume-ai-consent').check(); page.locator('#resume-ai-submit').click()
                        expect(page.locator('#resume-ai-results')).to_contain_text(excerpt)
                        page.get_by_role('button', name='Review this excerpt as a career fact').click()
                        expect(page.locator('#fact-statement')).to_have_value(excerpt)
                        self.assertEqual(page.locator('#fact-category').input_value(), 'projects')
                        page.locator('#fact-source').fill('Reviewed fictional résumé against records.')
                        page.get_by_role('button', name='Save unverified fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified')
                        self.assertEqual(len(calls), 2)
                    finally: browser.close()
            finally:
                release.set(); server.should_exit = True; thread.join(timeout=10); sock.close()
