import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from career_agent.lever_submit import application_url, reviewed_materials, submit
from career_agent.models import Job
from career_agent.storage import Store

URL = 'https://jobs.lever.co/example/01234567-1234-4234-8234-123456789abc'
DATA = dict(name='Candidate', email='candidate@example.invalid', phone='555-0100',
            filename='resume.pdf', content=base64.b64encode(b'%PDF-example').decode(),
            destination=URL+'/apply', approved=True)


class LeverSubmitTests(unittest.TestCase):
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
