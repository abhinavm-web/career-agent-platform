"""Browser-to-storage check of conflicting fictional work-arrangement evidence."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class RemoteConflictBrowserTests(unittest.TestCase):
    def test_saved_conflicting_posting_is_explained_on_board(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import CandidateProfile, Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            profile = root / 'profile.json'
            profile.write_text(json.dumps(asdict(CandidateProfile(('Engineer',), ('Python',), (), True))))
            job = Job('Fictional Company', 'Engineer', '', 'https://example.invalid/remote',
                      'Work arrangement: Hybrid\nPython', remote=True)
            with Store(db) as store: store.add_jobs([job])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', profile_path=profile))
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
                        expect(page.locator('#job-list')).to_contain_text('Remote details need review')
                        expect(page.locator('#job-list')).to_contain_text('90/100')
                        page.locator('#job-list button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('Conflicting remote signals')
                        expect(page.locator('#detail-panel')).to_contain_text('Work arrangement: Hybrid')
                        expect(page.locator('#detail-panel')).to_contain_text('remote credit withheld')
                        page.get_by_text('More filters', exact=True).click()
                        expect(page.locator('#remote-filter')).to_contain_text('Marked remote in saved job')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
