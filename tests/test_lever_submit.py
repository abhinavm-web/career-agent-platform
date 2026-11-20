import base64
import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from career_agent.lever_submit import application_url, reviewed_materials, submit
from career_agent.delivery import delivery_for_job, evidence, record_dispatch, record_preflight, resolve_dispatch
from career_agent.models import Job
from career_agent.storage import Store

URL = 'https://jobs.lever.co/example/01234567-1234-4234-8234-123456789abc'
DATA = dict(name='Candidate', email='candidate@example.invalid', phone='555-0100',
            filename='resume.pdf', content=base64.b64encode(b'%PDF-example').decode(),
            destination=URL+'/apply', approved=True)


class LeverSubmitTests(unittest.TestCase):
    def test_snapshot_rollback_and_concurrent_duplicate_guards(self):
        fields, content = reviewed_materials(DATA)
        jobs = [Job('Example', 'Example role', '', url) for url in (URL, URL+'/apply')]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'workspace.db'
            with Store(db) as store:
                store.add_jobs(jobs)
                record_preflight(store, jobs[0].id, fields['destination'], 'needs_user_action', 'Check the form.')
                store.connection.execute("""CREATE TEMP TRIGGER fail_snapshot BEFORE INSERT ON delivery_records
                    BEGIN SELECT RAISE(ABORT,'fixture write failure'); END""")
                with self.assertRaises(sqlite3.IntegrityError), store.connection:
                    store.connection.execute('BEGIN IMMEDIATE')
                    record_dispatch(store, jobs[0].id, fields, content, 'now')
                self.assertEqual(delivery_for_job(store, jobs[0])['state'], 'needs_user_action')
                self.assertIsNone(evidence(store, jobs[0].id)['requested_at'])

            def dispatch(job):
                with Store(db) as store, store.connection:
                    store.connection.execute('BEGIN IMMEDIATE')
                    try:
                        record_dispatch(store, job.id, fields, content, 'now')
                    except ValueError as error:
                        self.assertIn('already requested', str(error))
                        return False
                    return True
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(dispatch, jobs))
            self.assertEqual(results.count(True), 1)
            winner = jobs[results.index(True)]
            with Store(db) as store:
                self.assertEqual(delivery_for_job(store, jobs[results.index(False)])['owner_id'], winner.id)
                resolve_dispatch(store, winner.id, 'confirmed', 'private reference', None, 'candidate_verification')
                for _ in range(2):
                    resolve_dispatch(store, winner.id, 'confirmed', URL+'/thanks', None, 'employer_thanks_page')
                record = evidence(store, winner.id)
                self.assertEqual([v['receipt'] for v in record['verifications']], ['private reference', URL+'/thanks'])

    def test_local_state_follows_click_boundary_and_blocks_duplicate(self):
        class FixtureError(Exception):
            pass

        for mode, expected in [('additional_answers', 'needs_user_action'), ('changed_file', 'needs_user_action'),
                               ('launch_failure', 'failed_before_sending'),
                               ('click_timeout', 'awaiting_confirmation'), ('thanks', 'confirmed')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / 'workspace.db'
                job = Job('Example', 'Example role', '', URL)
                with Store(db) as store:
                    store.add_jobs([job])
                factory = MagicMock()
                launch = factory.return_value.__enter__.return_value.chromium.launch
                page = launch.return_value.new_page.return_value
                if mode == 'launch_failure':
                    launch.side_effect = FixtureError('fixture')
                page.url = DATA['destination']
                form = MagicMock()
                page.locator.side_effect = lambda selector: form if selector.startswith('form') else heading
                heading = MagicMock()
                heading.first.inner_text.return_value = job.title
                digest = hashlib.sha256(base64.b64decode(DATA['content'])).hexdigest()
                form.evaluate.side_effect = [True, mode != 'additional_answers', None if mode == 'changed_file' else digest]
                inputs = {}
                def field(selector):
                    control = inputs.setdefault(selector, MagicMock())
                    control.count.return_value = 0
                    for key in ('name', 'email', 'phone'):
                        if selector == f'input[name="{key}"]':
                            control.input_value.return_value = DATA[key]
                    return control
                form.locator.side_effect = field
                button = form.get_by_role.return_value
                button.get_attribute.side_effect = lambda key: 'btn-submit' if key == 'id' else None
                def click(**kwargs):
                    with Store(db) as store:
                        self.assertEqual(delivery_for_job(store, job)['state'], 'awaiting_confirmation')
                        pdf = store.connection.execute('SELECT pdf FROM delivery_records').fetchone()[0]
                        self.assertEqual(pdf, inputs['input[type=file][name=resume]'].set_input_files.call_args.args[0]['buffer'])
                    if mode == 'click_timeout':
                        raise FixtureError('uncertain click')
                    page.url = URL + '/thanks'
                button.click.side_effect = click
                with patch.dict('sys.modules', {'playwright.sync_api': SimpleNamespace(sync_playwright=factory, Error=FixtureError)}):
                    if mode in ('click_timeout', 'thanks'):
                        submit(db, job.id, DATA)
                        with self.assertRaises(ValueError):
                            submit(db, job.id, DATA)
                        self.assertEqual(button.click.call_count, 1)
                    else:
                        with self.assertRaises(ValueError):
                            submit(db, job.id, DATA)
                        button.click.assert_not_called()
                    self.assertEqual(factory.call_count, 1)
                with Store(db) as store:
                    self.assertEqual(delivery_for_job(store, job)['state'], expected)
                    record = evidence(store, job.id)
                    if mode in ('click_timeout', 'thanks'):
                        self.assertEqual(record['answers'], {k: DATA[k] for k in ('name', 'email', 'phone')})
                        self.assertTrue(record['approved_at'] and record['requested_at'])
                    else:
                        self.assertIsNone(record['requested_at'])
                        self.assertIsNone(record['answers'])
                    self.assertEqual(record['receipt'], URL+'/thanks' if mode == 'thanks' else None)

    def test_destination_and_approval(self):
        self.assertEqual(application_url(URL + '?tracking=value'), DATA['destination'])
        for bad in [URL.replace('https:', 'http:'), URL.replace('lever.co', 'lever.co.invalid'),
                    URL.replace('jobs.lever.co', 'user@jobs.lever.co'), URL+'/other']:
            with self.assertRaises(ValueError):
                application_url(bad)
        for changes in [dict(approved=False), dict(content='invalid'), dict(filename='../resume.pdf')]:
            with self.assertRaises(ValueError):
                reviewed_materials({**DATA, **changes})

    @unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable Chromium')
    def test_confirmed_or_uncertain_dispatch_cannot_repeat(self):
        from playwright.sync_api import BrowserType, Browser, Page, TimeoutError as BrowserTimeout
        real_launch, real_page, real_wait = BrowserType.launch, Browser.new_page, Page.wait_for_url
        form = """<h2>Example role</h2><form id="application-form" method="post">
        <input name="name"><input name="email"><input name="phone">
        <input name="resume" type="file" onchange="setTimeout(() => {
            this.form.org.value='Incorrect parsed employer';
            document.querySelector('.filename').textContent=this.files[0].name;
            document.querySelector('.resume-upload-success').hidden=false;
        },100)">
        <span class="visible-resume-upload" style="text-transform:uppercase"><span class="filename"></span></span>
        <span class="resume-upload-success" hidden>Success!</span><input name="org">
        <button type="button" id="btn-submit" onclick="if(!this.form.org.value) location.href='thanks'">Submit application</button></form>"""
        for confirm in (True, False):
            with self.subTest(confirm=confirm), tempfile.TemporaryDirectory() as directory:
                db = Path(directory)/'workspace.db'
                job = Job('Example', 'Example role', '', URL)
                with Store(db) as store:
                    store.add_jobs([job])
                def fixture(browser, **kwargs):
                    page = real_page(browser, **kwargs)
                    page.route('**/*', lambda r: r.fulfill(content_type='text/html',
                        body=form if r.request.url.endswith('/apply') else '<h1>Thank you for applying!</h1>'))
                    return page
                def wait(page, *args, **kwargs):
                    if not confirm:
                        raise BrowserTimeout('fixture has no receipt')
                    return real_wait(page, *args, **kwargs)
                with patch.object(BrowserType,'launch',lambda b, **kw:real_launch(b,headless=True)), \
                     patch.object(Browser,'new_page',fixture), patch.object(Page,'wait_for_url',wait):
                    result = submit(db, job.id, DATA)
                    self.assertEqual(result['status'], 'confirmed' if confirm else 'unconfirmed')
                    with self.assertRaises(ValueError):
                        submit(db, job.id, DATA)
                with Store(db) as store:
                    self.assertEqual(store.stage_of(job.id)=='submitted', confirm)
