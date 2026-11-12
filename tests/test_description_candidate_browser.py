"""Opt-in discovery stages source text for a separately reviewed correction."""
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class DescriptionCandidateBrowserTests(unittest.TestCase):
    def test_newest_sort_and_incomplete_score(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            older = Job.from_mapping({'company': 'Fictional Co', 'title': 'AI Transformation Manager',
                'job_url': 'https://example.invalid/older', 'description': 'AWS Python LangGraph AI governance', 'location': 'Tennessee'})
            newer = Job.from_mapping({'company': 'Fictional Co', 'title': 'Recent role',
                'job_url': 'https://example.invalid/newer', 'description': ''})
            with Store(db) as store: store.add_jobs([older, newer])
            profile = Path(__file__).resolve().parents[1] / 'examples' / 'profile.example.json'
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
                        page.locator('#nav-jobs').click()
                        expect(page.locator('#job-list button').first).to_contain_text('AI Transformation Manager')
                        page.get_by_label('Sort opportunities', exact=True).select_option('recent')
                        recent = page.locator('#job-list button').first
                        expect(recent).to_contain_text('Recent role')
                        expect(recent).to_contain_text('Incomplete')
                        self.assertIn('score incomplete', recent.get_attribute('aria-label'))
                        recent.click()
                        expect(page.locator('#detail-panel')).to_contain_text('keyword score is incomplete')
                        page.get_by_label('Sort opportunities', exact=True).select_option('score')
                        expect(page.locator('#job-list button').first).to_contain_text('AI Transformation Manager')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_changed_description_is_staged_only_with_extra_consent_and_reviewed(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                'job_url': 'https://example.invalid/role', 'description': 'Original fictional posting.', 'source': 'linkedin'})
            observed = replace(job, description='Updated fictional posting. <script>unsafe()</script>')
            with Store(db) as store: store.add_jobs([job])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.dashboard.discover_isolated', return_value=[observed]), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.on('dialog', lambda dialog: dialog.accept())
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.get_by_text('Find jobs with JobSpy', exact=True).click()
                        page.locator('#discover-query').fill('Fictional engineer')
                        page.locator('#discover-site-indeed').uncheck(); page.locator('#discover-site-zip').uncheck()
                        page.locator('#discover-details').check(); page.locator('#discover-consent').check()
                        page.get_by_role('button', name='Search and import jobs').click()
                        expect(page.locator('#discover-status')).to_contain_text('1 duplicates')
                        with Store(db) as store: self.assertEqual(store.description_candidates(job.id), [])
                        page.locator('#discover-stage-changes').check()
                        expect(page.locator('#discover-consent')).not_to_be_checked()
                        page.locator('#discover-consent').check()
                        page.get_by_role('button', name='Search and import jobs').click()
                        expect(page.locator('#discover-consent')).not_to_be_checked()
                        expect(page.locator('#job-list')).to_contain_text('Source text to review')
                        page.locator('#filter').select_option('source-text-review')
                        expect(page.locator('#visible-count')).to_have_text('1 opportunity')
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        page.get_by_role('tab', name='Overview').click()
                        page.get_by_text('Correct saved description', exact=True).click()
                        page.get_by_text('Review returned source descriptions', exact=True).click()
                        page.get_by_role('button', name='Load saved source text').click()
                        editor = page.locator('#job-description-editor')
                        expect(editor).to_contain_text(observed.description)
                        self.assertEqual(editor.locator('script').count(), 0)
                        page.get_by_role('button', name='Use this source text').click()
                        expect(editor.get_by_label('Corrected job description')).to_have_value(observed.description)
                        with Store(db) as store: self.assertEqual(store.get_job(job.id).description, job.description)
                        editor.get_by_label('I reviewed this corrected description.').check()
                        with page.expect_response(lambda response: response.request.method == 'PUT'
                                and response.url.endswith('/description')) as saved:
                            editor.get_by_role('button', name='Save corrected description').click()
                        self.assertEqual(saved.value.status, 200)
                        expect(page.locator('#visible-count')).to_have_text('0 opportunities')
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, observed.description)
                            self.assertEqual(store.description_revision(job.id), 1)
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_returned_description_requires_review_and_explicit_save(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                'job_url': 'https://example.invalid/role', 'description': '', 'source': 'linkedin'})
            with Store(db) as store:
                store.add_jobs([job, replace(job, title='Other role', url='https://example.invalid/other'),
                                replace(job, title='Complete role', url='https://example.invalid/complete', description='Existing description.')])
            observed = replace(job, description='Fictional source text. <script>unsafe()</script>')
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.dashboard.discover_isolated', return_value=[observed]), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.locator('#filter').select_option('missing-description')
                        expect(page.locator('#visible-count')).to_have_text('2 opportunities')
                        page.get_by_text('Find jobs with JobSpy', exact=True).click()
                        page.locator('#discover-query').fill('Fictional engineer')
                        page.locator('#discover-site-indeed').uncheck(); page.locator('#discover-site-zip').uncheck()
                        page.locator('#discover-details').check(); page.locator('#discover-consent').check()
                        page.get_by_role('button', name='Search and import jobs').click()
                        expect(page.locator('#discover-status')).to_contain_text('1 duplicates')
                        expect(page.locator('#job-list')).to_contain_text('Source text to review')
                        page.locator('#filter').select_option('source-text-review')
                        expect(page.locator('#visible-count')).to_have_text('1 opportunity')
                        expect(page.locator('#job-list')).not_to_contain_text('Other role')
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        page.get_by_role('tab', name='Overview').click()
                        page.get_by_text('Correct saved description', exact=True).click()
                        page.get_by_text('Review returned source descriptions', exact=True).click()
                        page.get_by_role('button', name='Load saved source text').click()
                        editor = page.locator('#job-description-editor')
                        expect(editor).to_contain_text(observed.description)
                        self.assertEqual(editor.locator('script').count(), 0)
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, '')
                        page.get_by_role('button', name='Use this source text').click()
                        expect(editor.get_by_label('Corrected job description')).to_have_value(observed.description)
                        expect(editor.get_by_label('I reviewed this corrected description.')).not_to_be_checked()
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, '')
                        editor.get_by_label('I reviewed this corrected description.').check()
                        editor.get_by_role('button', name='Save corrected description').click()
                        expect(page.locator('#detail-panel')).to_contain_text(observed.description)
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, observed.description)
                            self.assertEqual(store.description_revision(job.id), 1)
                        expect(page.locator('#visible-count')).to_have_text('0 opportunities')
                        page.locator('#filter').select_option('missing-description')
                        expect(page.locator('#visible-count')).to_have_text('1 opportunity')
                        expect(page.locator('#job-list')).to_contain_text('Other role')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
