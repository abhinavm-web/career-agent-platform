import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from career_agent.storage import Store
from test_dashboard import fake_job


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class OpportunityCleanupBrowserTests(unittest.TestCase):
    def test_duplicate_remove_expiry_archive_and_restore_in_browser(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'workspace.sqlite3'
            original = fake_job('https://careers.example.com/jobs/one')
            duplicate = replace(original, url='https://careers.example.com/jobs/two')
            with Store(db) as store: store.add_jobs([original, duplicate])
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
                        page = browser.new_page(); errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#job-list > button')).to_have_count(2)
                        page.locator('#job-list > button').first.click()
                        page.get_by_role('button', name='Find possible duplicates').click()
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='Remove this duplicate; keep selected role').click()
                        expect(page.locator('#job-list > button')).to_have_count(2)
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Remove this duplicate; keep selected role').click()
                        expect(page.locator('#job-list > button')).to_have_count(1)
                        page.reload(); page.locator('#nav-jobs').click()
                        expect(page.locator('#job-list > button')).to_have_count(1)
                        page.get_by_label('Filter opportunities').select_option('archived')
                        expect(page.locator('#job-list > button')).to_have_count(1)
                        page.locator('#job-list > button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('confirmed duplicate')
                        page.get_by_role('button', name='Restore opportunity').click()
                        expect(page.locator('#job-list > button')).to_have_count(0)
                        page.get_by_label('Filter opportunities').select_option('all')
                        expect(page.locator('#job-list > button')).to_have_count(2)
                        page.locator('#job-list > button').first.click()
                        posting = {'@type': 'JobPosting', 'url': duplicate.url, 'title': duplicate.title,
                                   'description': duplicate.description, 'hiringOrganization': {'name': duplicate.company},
                                   'validThrough': '2020-01-01T00:00:00Z'}
                        response = (200, ('<script type="application/ld+json">' + json.dumps(posting) + '</script>').encode(), 'text/html')
                        with patch('career_agent.posting_checks.public_get', return_value=response):
                            page.get_by_role('button', name='Check live availability', exact=True).click()
                            expect(page.locator('#job-list > button')).to_have_count(1)
                        expect(page.locator('#detail-panel')).to_contain_text('Archived: verified deadline passed')
                        page.get_by_label('Filter opportunities').select_option('archived')
                        expect(page.locator('#job-list > button')).to_have_count(1)
                        page.locator('#job-list > button').first.click()
                        # Restore from the tracker, then archive there: the board must
                        # update immediately, without a browser reload.
                        page.get_by_role('tab', name='Application', exact=True).click()
                        page.get_by_role('button', name='Track application progress', exact=True).click()
                        page.get_by_label('Next stage').select_option('saved')
                        page.get_by_role('button', name='Record stage', exact=True).click()
                        expect(page.locator('#crm-detail .detail-top')).to_contain_text('saved')
                        page.locator('#nav-jobs').click()
                        page.get_by_label('Filter opportunities').select_option('all')
                        expect(page.locator('#job-list > button')).to_have_count(2)
                        page.locator('#nav-tracker').click()
                        page.get_by_label('Next stage').select_option('archived')
                        page.get_by_role('button', name='Record stage', exact=True).click()
                        expect(page.locator('#crm-detail .detail-top')).to_contain_text('archived')
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#job-list > button')).to_have_count(1)
                        page.get_by_label('Filter opportunities').select_option('archived')
                        page.locator('#job-list > button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('archived in application tracker')
                        page.set_viewport_size({'width': 390, 'height': 844})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                        self.assertEqual(errors, [])
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
