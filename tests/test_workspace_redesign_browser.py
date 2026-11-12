"""Fictional end-to-end coverage for the redesigned workspace."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class WorkspaceRedesignTests(unittest.TestCase):
    def test_home_job_panels_and_settings_at_desktop_and_mobile_widths(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job('Fictional Co', 'Fictional Analyst', 'Boston', 'https://example.invalid/role',
                      'Requirements:\n- Python\nVisa sponsorship is available.')
            with Store(db) as store:
                store.add_jobs([job])
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
                        for viewport in ({'width': 1440, 'height': 900}, {'width': 390, 'height': 844}):
                            page = browser.new_page(viewport=viewport)
                            page.goto(f'http://127.0.0.1:{port}')
                            expect(page.get_by_role('heading', name='Your next move, in focus.')).to_be_visible()
                            expect(page.locator('#home-recommendations')).to_contain_text('Fictional Analyst')
                            page.get_by_role('button', name='Explore opportunities').click()
                            expect(page.get_by_role('button', name='View Fictional Analyst at Fictional Co')).to_be_visible()
                            page.get_by_role('button', name='View Fictional Analyst at Fictional Co').click()
                            expect(page.get_by_role('tab', name='Overview')).to_have_attribute('aria-selected', 'true')
                            page.get_by_role('tab', name='Fit').click()
                            expect(page.get_by_role('button', name='Compare requirements')).to_be_visible()
                            page.get_by_role('tab', name='Sponsorship').click()
                            expect(page.locator('#job-pane-sponsorship')).to_contain_text('Posting mentions sponsorship')
                            page.get_by_role('tab', name='Application').click()
                            expect(page.get_by_role('button', name='Analyze this role with AI')).to_be_visible()
                            expect(page.get_by_role('button', name='Track application progress')).to_be_visible()
                            page.get_by_role('tab', name='Application').press('Home')
                            expect(page.get_by_role('tab', name='Overview')).to_be_focused()
                            page.get_by_role('button', name='Career profile', exact=True).click()
                            page.get_by_role('button', name='Import a résumé').click()
                            expect(page.locator('#resume-file')).to_be_focused()
                            page.get_by_role('button', name='Settings & data').click()
                            expect(page.get_by_role('heading', name='Backup & privacy')).to_be_visible()
                            page.close()
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
