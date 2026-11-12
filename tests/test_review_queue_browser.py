"""Open a fictional suggestion and explicitly create a local follow-up."""
import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ReviewQueueBrowserTests(unittest.TestCase):
    def test_suggestion_opens_opportunity_without_creating_task(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.crm import ApplicationCRM
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from test_crm import sample_job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'test.sqlite3'; job = sample_job()
            with Store(db) as store:
                store.add_jobs([job]); store.stage(job.id)
                store.confirm(job.id, 'fictional-reference', attested=True)
                ApplicationCRM(store)
                old = (datetime.now(timezone.utc) - timedelta(days=20)).strftime('%Y-%m-%d %H:%M:%S')
                with store.connection:
                    store.connection.execute('UPDATE applications SET updated_at=? WHERE job_id=?', (old, job.id))
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
                        page.get_by_role('button', name='Application tracker', exact=True).click()
                        summary = page.locator('#crm-summary')
                        expect(summary).to_contain_text('Applications to review')
                        expect(summary).to_contain_text('20 calendar days ago')
                        with Store(db) as store:
                            self.assertEqual(ApplicationCRM(store).followups(job.id), [])
                        summary.get_by_role('button', name='AI Transformation Manager · Example Employer').click()
                        expect(page.locator('#crm-detail')).to_contain_text('AI Transformation Manager')
                        with Store(db) as store:
                            self.assertEqual(ApplicationCRM(store).followups(job.id), [])
                        page.get_by_label('Follow-up title').fill('Review next step')
                        page.get_by_label('Due date', exact=True).fill('2030-01-01')
                        page.get_by_role('button', name='Add follow-up').click()
                        expect(summary).to_contain_text('No applications currently meet these criteria.')
                        with Store(db) as store:
                            self.assertEqual(ApplicationCRM(store).followups(job.id)[0]['title'], 'Review next step')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
