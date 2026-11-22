"""Browser coverage for local follow-up pagination and actions."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class FollowupPagesBrowserTests(unittest.TestCase):
    def test_open_and_job_history_pages_are_accessible_and_local(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        from career_agent.crm import ApplicationCRM
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job('Fictional Co', 'Engineer', 'Boston', 'https://example.invalid/role')
            with Store(db) as store:
                store.add_jobs([job]); crm = ApplicationCRM(store)
                contact = crm.add_contact(job.id, 'Fictional recruiter')
                tasks = [crm.add_followup(job.id, f'Task {i:03d}', '2027-12-01', contact['contact_id'] if i == 0 else None) for i in range(105)]
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
                        page = browser.new_page()
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#job-list')).to_contain_text('Engineer')
                        page.get_by_role('button', name='Application tracker', exact=True).click()
                        summary = page.locator('#crm-summary')
                        expect(summary).to_contain_text('Showing the first 6 of 105 open follow-ups.')
                        summary.get_by_role('button', name='Browse all open follow-ups').click()
                        expect(summary).to_contain_text('Open follow-ups 1–20 of 105')
                        summary.get_by_role('button', name='Next follow-ups', exact=True).click()
                        expect(summary).to_contain_text('Open follow-ups 21–40 of 105')
                        task = summary.locator('.crm-record').filter(has_text='Task 020')
                        page.once('dialog', lambda dialog: dialog.accept())
                        task.get_by_role('button', name='Mark completed').click()
                        expect(summary).to_contain_text('Showing the first 6 of 104 open follow-ups.')
                        with Store(db) as store:
                            row = store.connection.execute('SELECT completed_at FROM crm_followups WHERE followup_id=?', (tasks[20]['followup_id'],)).fetchone()
                            self.assertIsNotNone(row['completed_at'])
                        page.locator('#crm-jobs button').first.click()
                        detail = page.locator('#crm-detail')
                        expect(detail).to_contain_text('Showing 1–100 of 105 follow-ups')
                        page.once('dialog', lambda dialog: dialog.accept())
                        detail.get_by_role('button', name='Next job follow-ups').click()
                        expect(detail).to_contain_text('Showing 101–105 of 105 follow-ups')
                        expect(detail).to_contain_text('Completed')
                        page.once('dialog', lambda dialog: dialog.accept())
                        detail.get_by_role('button', name='Previous job follow-ups').click()
                        expect(detail).to_contain_text('Showing 1–100 of 105 follow-ups')
                        expect(detail).to_contain_text('Fictional recruiter')
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        detail.get_by_role('button', name='Remove contact').click()
                        expect(detail).to_contain_text('Fictional recruiter')
                        page.once('dialog', lambda dialog: dialog.accept())
                        detail.get_by_role('button', name='Remove contact').click()
                        expect(detail).not_to_contain_text('Fictional recruiter')
                        with Store(db) as store:
                            crm = ApplicationCRM(store)
                            self.assertEqual(crm.contacts(job.id), [])
                            saved = next(task for task in crm.followups(job.id) if task['followup_id'] == tasks[0]['followup_id'])
                            self.assertIsNone(saved['contact_id'])
                            self.assertEqual(saved['title'], 'Task 000')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
