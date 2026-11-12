"""Fictional browser capture must be reviewed before storage."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class CaptureBrowserTests(unittest.TestCase):
    def test_capture_requires_review_and_preserves_unsaved_fields(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store

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
                        page.get_by_role('button', name='+ Add opportunity').click()
                        page.get_by_text('Paste a browser capture', exact=True).click()
                        capture = {'format':'career-agent-job-capture-v1', 'title':'Fictional Analyst',
                                   'url':'https://example.invalid/jobs/analyst', 'selection':'Python experience. <script>unsafe()</script>'}
                        page.locator('#job-capture-input').fill(json.dumps(capture))
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#add-job-form [name="title"]')).to_have_value('Fictional Analyst')
                        expect(page.locator('#add-job-form [name="company"]')).to_have_value('')
                        with Store(db) as store: self.assertEqual(len(store.list_jobs()), 0)
                        page.locator('#job-capture-input').fill(json.dumps({**capture, 'title':'Replacement'}))
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#add-job-form [name="title"]')).to_have_value('Fictional Analyst')
                        page.locator('#job-capture-input').fill(json.dumps({**capture, 'url':'https://user:secret@example.invalid/job'}))
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#job-capture-status')).to_contain_text('without credentials')
                        page.locator('#add-job-form [name="company"]').fill('Fictional Co')
                        page.get_by_role('button', name='Save opportunity').click()
                        expect(page.locator('#job-list')).to_contain_text('Fictional Analyst')
                        with Store(db) as store:
                            jobs = store.list_jobs(); self.assertEqual(len(jobs), 1)
                            self.assertEqual(jobs[0].description, capture['selection'])
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
