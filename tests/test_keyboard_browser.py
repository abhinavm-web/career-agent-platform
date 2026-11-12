"""Keyboard focus behavior in an isolated fictional workspace."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class KeyboardBrowserTests(unittest.TestCase):
    def test_navigation_form_close_and_search_focus(self):
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
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.keyboard.press('Tab')
                        expect(page.get_by_role('link', name='Skip to workspace')).to_be_focused()
                        page.keyboard.press('Enter')
                        expect(page.locator('#workspace')).to_be_focused()
                        page.locator('#nav-h1b').click()
                        expect(page.locator('#h1b-screen h2')).to_be_focused()
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#jobs-screen h2')).to_be_focused()
                        page.locator('#show-add-job').click()
                        expect(page.locator('#add-job-form [name="title"]')).to_be_focused()
                        page.locator('#close-add-job').click()
                        expect(page.locator('#show-add-job')).to_be_focused()
                        page.locator('#show-add-job').click()
                        page.locator('#add-job-form [name="title"]').fill('Fictional analyst')
                        page.locator('#add-job-form [name="company"]').fill('Example company')
                        page.locator('#add-job-form [name="job_url"]').fill('https://example.invalid/role')
                        page.get_by_role('button', name='Save opportunity').click()
                        expect(page.locator('#notice')).to_contain_text('Opportunity added')
                        expect(page.locator('#show-add-job')).to_be_focused()
                        page.locator('#search').focus()
                        expect(page.locator('#search')).to_be_focused()
                        self.assertNotEqual(page.locator('#search').evaluate('(el) => getComputedStyle(el).outlineStyle'), 'none')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
