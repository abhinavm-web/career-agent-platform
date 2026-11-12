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
                        metadata = {'job_title': 'Structured Analyst', 'company': 'Page-supplied Co'}
                        page.locator('#job-capture-input').fill(json.dumps({**capture, 'metadata': metadata}))
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#add-job-form [name="title"]')).to_have_value('Structured Analyst')
                        expect(page.locator('#add-job-form [name="company"]')).to_have_value('Page-supplied Co')
                        expect(page.locator('#job-capture-status')).to_contain_text('unverified')
                        page.locator('#add-job-form [name="company"]').fill('Existing Co')
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#add-job-form [name="company"]')).to_have_value('Existing Co')
                        page.locator('#job-capture-input').fill(json.dumps({**capture, 'metadata': {'company': ['bad']}}))
                        page.get_by_role('button', name='Fill job fields').click()
                        expect(page.locator('#job-capture-status')).to_contain_text('Invalid browser capture metadata')
                        page.locator('#job-capture-input').fill(json.dumps(capture))
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Fill job fields').click()
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
                        page.get_by_role('button', name='View Fictional Analyst at Fictional Co').click()
                        page.get_by_text('Correct saved description', exact=True).click()
                        page.get_by_text('Use a browser capture for this job', exact=True).click()
                        editor = page.locator('#job-description-editor')
                        capture_field = editor.get_by_label('Browser capture JSON')
                        description = editor.get_by_label('Corrected job description')
                        capture_field.fill(json.dumps({**capture, 'url': 'https://example.invalid/jobs/other', 'selection': 'Updated fictional description.'}))
                        page.once('dialog', lambda dialog: dialog.accept())
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(editor).to_contain_text('Capture URL does not match')
                        expect(description).to_have_value(capture['selection'])
                        capture_field.fill(json.dumps({**capture, 'selection': 'Updated fictional description.'}))
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(description).to_have_value(capture['selection'])
                        page.once('dialog', lambda dialog: dialog.accept())
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(description).to_have_value('Updated fictional description.')
                        with Store(db) as store:
                            self.assertEqual(store.get_job(jobs[0].id).description, capture['selection'])
                        checkbox = editor.get_by_label('I reviewed this corrected description.')
                        checkbox.check(); description.fill('Updated fictional description, reviewed.')
                        expect(checkbox).not_to_be_checked()
                        checkbox.check(); editor.get_by_role('button', name='Save corrected description').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Updated fictional description, reviewed.')
                        with Store(db) as store:
                            self.assertEqual(store.get_job(jobs[0].id).description, 'Updated fictional description, reviewed.')
                            self.assertEqual(store.description_revision(jobs[0].id), 1)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
