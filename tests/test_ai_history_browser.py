"""Browser journey for a consented synthetic provider test and local request cap."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class AIHistoryBrowserTests(unittest.TestCase):
    def test_request_limit_and_private_history(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1', 'daily_request_limit': 1}}
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
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value='OK') as complete, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-ai').click()
                        expect(page.locator('#ai-test-route')).to_be_enabled()
                        expect(page.locator('#ai-destination')).to_contain_text('daily local request limit 1')
                        page.locator('#ai-test-consent').check(); page.locator('#ai-test-route').click()
                        expect(page.locator('#ai-test-status')).to_contain_text('provider returned a response')
                        page.locator('#ai-test-consent').check(); page.locator('#ai-test-route').click()
                        expect(page.locator('#ai-test-status')).to_contain_text('daily request limit reached')
                        self.assertEqual(complete.call_count, 1)
                        page.get_by_text('Local AI request history and limits').click()
                        page.locator('#ai-history-refresh').click()
                        expect(page.locator('#ai-history')).to_contain_text('local: 1 attempts')
                        expect(page.locator('#ai-history')).to_contain_text('route_test · completed')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
