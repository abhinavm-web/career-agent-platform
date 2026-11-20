"""Exercise the companion's real popup and form code with all web traffic intercepted."""
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'browser-companion'
FORM_URL = 'https://jobs.lever.co/example/01234567-1234-4234-8234-123456789abc/apply'
FORM = """<h2>Fictional Engineer</h2><form id="application-form" method="post">
<label>Name<input name="name" type="text"></label><label>Email<input type="email" name="email" required></label>
<label>Resume<input name="resume" type="file"></label><input type="hidden" name="opaque" value="private-marker">
<button type="button" id="btn-submit" onclick="window.clicks=(window.clicks||0)+1">Submit application</button></form>"""


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class SupervisedApplicationTests(unittest.TestCase):
    def test_fill_review_change_approve_and_replay(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context()
            context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FORM))
            employer = context.new_page()
            employer.goto(FORM_URL)
            popup = context.new_page()
            popup.goto('https://popup.example.invalid/')
            popup.set_content((ROOT / 'popup.html').read_text(encoding='utf8').split('<script')[0])
            # The browser supplies chrome.* only to extensions. Bridge that transport
            # to a distinct real page to test the unchanged popup and injected logic.
            def execute(options):
                result = employer.evaluate('([fn,args])=>eval("("+fn+")")(...args)', [options['fn'], options['args']])
                return [{'documentId': 'fixture-document', 'result': result}]

            popup.expose_function('executeOnFixture', execute)
            popup.evaluate("""window.chrome={tabs:{query:async()=>[{id:1}]}, scripting:{
                executeScript:async o=>executeOnFixture({fn:o.func.toString(),args:o.args})}}""")
            popup.add_script_tag(path=str(ROOT / 'application.js'))
            popup.locator('summary').click()
            popup.get_by_label('Full name').fill('Example Candidate')
            popup.get_by_label('Email', exact=True).fill('candidate@example.invalid')
            popup.get_by_text('Fill empty contact fields').click()
            expect(employer.locator('[name=name]')).to_have_value('Example Candidate')
            employer.locator('[type=file]').set_input_files({'name': 'fictional.txt', 'mimeType':'text/plain', 'buffer':b'Example resume'})
            review = popup.locator('#review-application')
            approve = popup.locator('#send-application')
            status = popup.locator('#application-status')
            review.click()
            expect(approve).to_be_visible()
            expect(popup.locator('#application-preview')).to_contain_text('fictional.txt')
            expect(popup.locator('#application-preview')).not_to_contain_text('private-marker')
            self.assertIsNone(employer.evaluate('window.clicks'))
            employer.locator('[name=email]').fill('updated@example.invalid')
            approve.click()
            expect(status).to_contain_text('changed')
            self.assertIsNone(employer.evaluate('window.clicks'))
            review.click()
            expect(popup.locator('#application-preview')).to_contain_text('updated@example.invalid')
            employer.locator('[type=file]').set_input_files({'name': 'fictional.txt', 'mimeType': 'text/plain', 'buffer': b'Changed content'})
            approve.click()
            expect(status).to_contain_text('changed')
            self.assertIsNone(employer.evaluate('window.clicks'))
            review.click()
            expect(approve).to_be_visible()
            approve.click()
            expect(status).to_contain_text('Submission requested')
            self.assertEqual(employer.evaluate('window.clicks'), 1)
            review.click()
            expect(status).to_contain_text('already requested')
            expect(approve).to_be_hidden()
            employer.goto(FORM_URL.replace('jobs.lever.co','example.invalid'))
            review.click()
            expect(status).to_contain_text('Lever application form first')
            expect(approve).to_be_hidden()
            browser.close()
