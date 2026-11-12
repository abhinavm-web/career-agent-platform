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
    def test_structured_capture_review_to_matching_and_storage(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.candidate import FactBank
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import CandidateProfile, Job
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from test_dashboard import fake_fact_bank

        popup = (Path(__file__).resolve().parents[1] / 'browser-companion' / 'popup.js').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                                    'job_url': 'https://www.linkedin.com/jobs/view/1234567890', 'description': ''})
            with Store(db) as store:
                store.add_jobs([job])
                CandidateWorkspace(store).import_legacy(CandidateProfile(('Engineer',), ('Python',), (), True), FactBank.parse(fake_fact_bank()))
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                posting = {'@type': 'JobPosting', 'title': 'Engineer', 'hiringOrganization': {'name': 'Fictional Co'},
                           'description': '<p>Overview</p><img src="https://tracking.invalid/pixel"><h2>Requirements</h2><ul><li><p>Python\n experience</p></li><li>Rust is not required</li></ul>'}
                body = ('<title>Engineer</title><button id="capture">Selected</button>'
                        '<button id="capture-structured">Structured</button><p id="status"></p>'
                        '<script type="application/ld+json">' + json.dumps(posting) + '</script>')
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page()
                        attempted = []
                        page.route('https://tracking.invalid/**', lambda route: (attempted.append(route.request.url), route.abort()))
                        page.route('https://www.linkedin.com/**', lambda route: route.fulfill(status=200, content_type='text/html', body=body))
                        page.goto('https://www.linkedin.com/jobs/view/fictional-engineer-1234567890?refId=fictional')
                        page.evaluate('''() => {
                            window.copiedCapture = null;
                            Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { window.copiedCapture = value; }}});
                            window.chrome = {tabs: {query: async () => [{id: 1}]}, scripting: {executeScript: async ({func, args}) => [{result: func(...args)}]}};
                        }''')
                        page.add_script_tag(content=popup); page.locator('#capture-structured').click()
                        expect(page.locator('#status')).to_contain_text('Copied structured')
                        captured = page.evaluate('window.copiedCapture')
                        self.assertEqual(json.loads(captured)['selection'], 'Overview\nRequirements\n- Python experience\n- Rust is not required')
                        page.wait_for_timeout(100)
                        self.assertEqual(attempted, [])
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='View Engineer at Fictional Co').click()
                        page.get_by_role('tab', name='Overview').click()
                        page.get_by_text('Correct saved description', exact=True).click()
                        page.get_by_text('Use a browser capture for this job', exact=True).click()
                        editor = page.locator('#job-description-editor')
                        editor.get_by_label('Browser capture JSON').fill(captured)
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(editor.get_by_label('Corrected job description')).to_have_value(json.loads(captured)['selection'])
                        expect(editor).to_contain_text('LinkedIn URL job numbers match')
                        expect(editor.get_by_label('I reviewed this corrected description.')).not_to_be_checked()
                        with Store(db) as store: self.assertEqual(store.get_job(job.id).description, '')
                        editor.get_by_label('I reviewed this corrected description.').check()
                        editor.get_by_role('button', name='Save corrected description').click()
                        expect(page.locator('#detail-panel')).to_contain_text('- Python experience')
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, json.loads(captured)['selection'])
                            self.assertIn('Structured JobPosting', store.description_latest(job.id)['source_note'])
                        evidence = page.locator('#fit-evidence')
                        page.get_by_role('tab', name='Fit').click()
                        evidence.get_by_role('button', name='Compare requirements').click()
                        expect(evidence).to_contain_text('Labeled required')
                        expect(evidence).to_contain_text('- Python experience')
                        expect(evidence).to_contain_text('Potentially relevant verified fact experience-01')
                        qualified = evidence.locator('.fit-evidence-item').filter(has_text='Rust is not required')
                        expect(qualified).to_contain_text('Qualification level unspecified')
                        expect(qualified).to_contain_text('optional or negated wording')
                        expect(qualified).not_to_contain_text('Labeled required')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_structured_capture_reads_only_a_single_inert_job_description(self):
        from playwright.sync_api import sync_playwright, expect
        popup = (Path(__file__).resolve().parents[1] / 'browser-companion' / 'popup.js').read_text(encoding='utf-8')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.route('https://example.invalid/**', lambda route: route.fulfill(status=200, content_type='text/html', body='''
                    <title>Fictional role</title><button id="capture">Selected</button>
                    <button id="capture-structured">Structured</button><p id="status"></p>
                    <script type="application/ld+json">{"@type":"JobPosting","title":"Engineer",
                    "hiringOrganization":{"name":"Fictional Co"},"description":"<p>Build &amp; review</p><script>window.unsafeRan=true<\\/script><ul><li>Python</li></ul>"}</script>'''))
                page.goto('https://example.invalid/job')
                page.evaluate('''() => {
                    window.copiedCapture = null; window.pageReads = 0;
                    Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { window.copiedCapture = value; }}});
                    window.chrome = {tabs: {query: async () => [{id: 1}]}, scripting: {executeScript: async ({func, args}) => {
                      window.pageReads++; return [{result: func(...args)}];
                    }}};
                }''')
                page.add_script_tag(content=popup)
                self.assertEqual(page.evaluate('window.pageReads'), 0)
                page.locator('#capture-structured').click()
                expect(page.locator('#status')).to_contain_text('Copied structured')
                capture = json.loads(page.evaluate('window.copiedCapture'))
                self.assertEqual(capture['selection'], 'Build & review\n- Python')
                self.assertEqual(capture['text_source'], 'structured_description')
                self.assertEqual(capture['metadata']['company'], 'Fictional Co')
                self.assertFalse(page.evaluate('Boolean(window.unsafeRan)'))
            finally:
                browser.close()

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
                        page.locator('#nav-jobs').click()
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
                        page.get_by_role('tab', name='Overview').click()
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
                        capture_field.fill(json.dumps({**capture, 'selection': 'Updated fictional description.', 'text_source': 'structured_description'}))
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(description).to_have_value(capture['selection'])
                        page.once('dialog', lambda dialog: dialog.accept())
                        editor.get_by_role('button', name='Fill description from capture').click()
                        expect(description).to_have_value('Updated fictional description.')
                        expect(editor.get_by_label('Source note (optional)')).to_have_value('Structured JobPosting on captured page; source not independently verified.')
                        expect(editor).to_contain_text('may be incomplete or describe another job')
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
