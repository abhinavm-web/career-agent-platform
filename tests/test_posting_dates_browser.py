"""A fictional source-reported posting date survives discovery and reload."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class PostingDatesBrowserTests(unittest.TestCase):
    def test_manual_discovery_shows_source_date_without_implying_availability(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.discovery import DiscoveryResults
        from career_agent.models import Job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer', 'job_url': 'https://example.invalid/role',
                                    'description': 'Fictional posting', 'site': 'indeed', 'date_posted': '2025-02-03'})
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with patch('career_agent.dashboard.discover_isolated', return_value=DiscoveryResults([job])), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_text('Find jobs with JobSpy', exact=True).click()
                        page.locator('#discover-query').fill('Fictional engineer')
                        page.locator('#discover-consent').check()
                        page.get_by_role('button', name='Search and import jobs').click()
                        expect(page.locator('#discover-status')).to_contain_text('1 added')
                        page.reload()
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        detail = page.locator('#detail-panel')
                        expect(detail).to_contain_text('Source-reported posting date: 2025-02-03')
                        expect(detail).to_contain_text('does not establish that the role is open')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
