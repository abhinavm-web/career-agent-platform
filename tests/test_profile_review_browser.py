"""The application asks, saves and verifies answers, then generates and exports documents."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from career_agent.candidate import FactBank
from career_agent.models import CandidateProfile
from career_agent.storage import Store
from career_agent.workspace import CandidateWorkspace
from test_dashboard import fake_fact_bank, fake_job
from test_document_revisions import ROUTE
from test_ai_revision_browser import fictional_validation
from test_profile_review import QUESTIONS, RESUME


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable installed Chromium')
class ProfileReviewBrowserTests(unittest.TestCase):
    def test_app_questions_answers_verification_generation_and_four_exports(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import create_app, DashboardConfig
        from docx import Document
        quality = 'Reduced elapsed QA cycle time by 25%.'
        skill = 'Used FictionalSearch for prototyping, evaluation and production implementation.'
        prompts = []
        def complete(route, prompt, **kwargs):
            data = json.loads(prompt); prompts.append(data)
            if 'saved_facts' in data: return json.dumps(QUESTIONS)
            claims = data['verified_facts']
            rows = [{'text': text, 'fact_ids': [id], 'section': 'skills' if 'FictionalSearch' in text else 'experience'} for id, text in claims.items()]
            return json.dumps({'resume': rows, 'cover_letter': [{'text': quality + ' ' + skill, 'fact_ids': list(claims)}]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'workspace.sqlite3'
            with Store(db) as store:
                store.add_jobs([fake_job()])
                CandidateWorkspace(store).import_legacy(CandidateProfile(('AI Program Lead',), ('Python',), (), True), FactBank.parse(fake_fact_bank()))
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', side_effect=complete), patch('career_agent.resume_validation.validate', side_effect=fictional_validation), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000}); errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{port}/classic')
                        page.locator('#nav-profile').click()
                        page.locator('#profile-review summary').first.click()
                        page.locator('#profile-review-load').click()
                        expect(page.locator('#profile-review-generate')).to_be_enabled()
                        page.locator('#profile-review-file').set_input_files({'name': 'fictional-resume.txt', 'mimeType': 'text/plain', 'buffer': RESUME.encode()})
                        page.locator('#profile-review-extract').click()
                        expect(page.locator('#profile-review-resume')).to_have_value(RESUME)
                        page.locator('#profile-review-generate').click()
                        expect(page.locator('#profile-review-status')).to_contain_text('Confirm sharing')
                        self.assertEqual(prompts, [])
                        page.locator('#profile-review-consent').check()
                        page.locator('#profile-review-generate').click()
                        cards = page.locator('.profile-review-question')
                        expect(cards).to_have_count(2)
                        first, second = cards.nth(0), cards.nth(1)
                        second.get_by_label('Your accurate career statement').fill(skill)
                        first.get_by_label('Your accurate career statement').fill(quality)
                        first.get_by_label('How should this answer').select_option('experience-01')
                        expect(first.get_by_role('button', name='Save answer for verification')).to_be_disabled()
                        first.get_by_label('I reviewed this wording').check()
                        first.get_by_role('button', name='Save answer for verification').click()
                        expect(first).to_contain_text('Saved as unverified')
                        expect(second.get_by_label('Your accurate career statement')).to_have_value(skill)
                        first.get_by_label('I confirm this saved statement').check()
                        first.get_by_role('button', name='Verify saved answer').click()
                        expect(first).to_contain_text('Verified by you')
                        expect(second.get_by_label('Your accurate career statement')).to_have_value(skill)
                        second.get_by_label('Answer category').select_option('skills')
                        second.get_by_label('How should this answer').select_option('')
                        second.get_by_label('I reviewed this wording').check()
                        second.get_by_role('button', name='Save answer for verification').click()
                        expect(second).to_contain_text('Saved as unverified')
                        second.get_by_label('I confirm this saved statement').check()
                        second.get_by_role('button', name='Verify saved answer').click()
                        expect(second).to_contain_text('Verified by you')
                        page.reload(); page.locator('#nav-profile').click()
                        page.locator('#profile-review summary').first.click(); page.locator('#profile-review-load').click()
                        expect(page.locator('.profile-review-question').filter(has_text='Verified by you')).to_have_count(2)
                        page.set_viewport_size({'width': 390, 'height': 844})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                        page.set_viewport_size({'width': 1440, 'height': 1000})
                        page.locator('#nav-jobs').click(); page.locator('#job-list button').first.click()
                        page.get_by_role('tab', name='Application', exact=True).click()
                        page.get_by_role('button', name='Open application studio').click()
                        studio = page.locator('.document-revision-studio')
                        studio.get_by_role('button', name='Load models and saved revisions').click()
                        expect(studio.get_by_label('Revision model')).to_have_value('claude')
                        studio.get_by_label('I consent to sharing', exact=False).check()
                        studio.get_by_role('button', name='Generate resume and cover letter').click()
                        expect(studio).to_contain_text('Validation saved.')
                        self.assertEqual(set(prompts[-1]['verified_facts'].values()), {quality, skill})
                        studio.get_by_label('I reviewed both AI documents', exact=False).check()
                        studio.get_by_role('button', name='Confirm AI revision for export').click()
                        for kind in ('resume', 'cover letter'):
                            for fmt in ('PDF', 'DOCX'):
                                if fmt == 'PDF' and os.environ.get('CAREER_AGENT_TEST_PDF') != '1': continue
                                with page.expect_download() as download:
                                    studio.get_by_role('button', name=f'Download reviewed AI {kind} {fmt}', exact=True).click()
                                file = Path(download.value.path())
                                if fmt == 'DOCX': text = ' '.join(x.text for x in Document(file).paragraphs)
                                else:
                                    from pypdf import PdfReader
                                    text = ' '.join(x.extract_text() for x in PdfReader(file).pages)
                                text = ' '.join(text.split())
                                self.assertIn(quality, text); self.assertIn(skill, text)
                                self.assertNotIn('Private fictional', text)
                        self.assertEqual(len(prompts), 2)
                        self.assertEqual(errors, [])
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
