"""Browser exercise of fact-backed application review with fictional inputs."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from career_agent.models import CandidateProfile
from career_agent.storage import Store
from test_dashboard import fake_fact_bank, fake_job
from unittest.mock import patch
from test_document_revisions import ROUTE, OUTPUT


def fictional_validation(snapshot, revision, route_name, **kwargs):
    """Fictional semantic results; browser test covers orchestration and review gates."""
    from career_agent.fit_evidence import requirement_inventory
    requirements = []
    for row in requirement_inventory(snapshot['description'])['requirements']:
        requirements.append({**row, 'status': 'supported' if 'Python' in row['quote'] or 'AWS' in row['quote'] else 'not_demonstrated',
                             'resume_indices': [0] if 'Python' in row['quote'] or 'AWS' in row['quote'] else [],
                             'profile_fact_ids': [], 'reason': 'Fictional evidence assessment; unmatched requirements remain visible.'})
    return {'status': 'completed', 'complete': True, 'export_allowed': True, 'requirements': requirements,
            'claims': {'complete': True, 'passages': [{'kind': kind, 'index': i, 'status': 'supported', 'reason': 'Supported by the cited fictional statement.'}
                       for kind in ('resume', 'cover_letter') for i, row in enumerate(revision['document'][kind])]},
            'notices': [], 'created_at': '2025-01-01T00:00:00+00:00', 'model': 'fictional-model', 'provider': 'anthropic'}


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class AIRevisionBrowserTests(unittest.TestCase):
    def test_ai_revision_model_selection_review_download_and_reload(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.candidate import FactBank
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.workspace import CandidateWorkspace
        from docx import Document
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'workspace.sqlite3'
            from dataclasses import replace
            job = replace(fake_job(), description='Requirements:\n- Python\n- AWS\n**Specialized Role Requirements**\n- Novel expertise')
            with Store(db) as store:
                store.add_jobs([job])
                CandidateWorkspace(store).import_legacy(CandidateProfile(('AI Program Lead',), ('Python',), (), True), FactBank.parse(fake_fact_bank()))
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)) as complete, patch('career_agent.resume_validation.validate', side_effect=fictional_validation) as validate, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.locator('#job-list button').first.click()
                        page.get_by_role('tab', name='Application').click()
                        page.get_by_role('button', name='Open application studio').click()
                        panel = page.locator('.document-revision-studio')
                        panel.locator('summary').first.click()
                        panel.get_by_role('button', name='Load models and saved revisions').click()
                        expect(panel.get_by_label('Revision model')).to_have_value('claude')
                        panel.get_by_label('Revision instructions').fill('Emphasize relevant delivery work.')
                        panel.get_by_role('button', name='Generate resume and cover letter').click()
                        complete.assert_not_called()
                        complete.return_value = json.dumps({'requirements': [{'quote': '- Python', 'fact_ids': [], 'reason': 'Ask about further Python experience.'}]})
                        panel.get_by_label('I consent to sharing', exact=False).check()
                        panel.get_by_role('button', name='Find experience to add').click()
                        question = panel.locator('.experience-questions .inline-suggestion').first
                        expect(question).to_contain_text('No evidence linked by AI')
                        question.get_by_label('Your experience').fill('Created Python training materials for a documented workshop.')
                        question.get_by_role('button', name='Save answer as unverified fact').click()
                        expect(question).to_contain_text('Saved, not verified:')
                        expect(question.get_by_role('button', name='Verify this new fact')).to_be_disabled()
                        question.get_by_label('I checked this saved statement', exact=False).check()
                        question.get_by_role('button', name='Verify this new fact').click()
                        expect(question).to_contain_text('Fact verified.')
                        complete.return_value = json.dumps(OUTPUT)
                        panel.get_by_label('I consent to sharing', exact=False).check()
                        panel.get_by_role('button', name='Generate resume and cover letter').click()
                        expect(panel).to_contain_text('Validation saved.')
                        expect(panel).to_contain_text('Delivered an AI program using Python and AWS.')
                        expect(panel).to_contain_text('My experience delivering an AI program')
                        expect(panel.get_by_role('button', name='Confirm AI revision for export')).to_be_disabled()
                        self.assertIn('Created Python training materials', complete.call_args.args[1])
                        self.assertIsNone(json.loads(complete.call_args.args[1])['previous_unverified_draft'])
                        expect(panel).to_contain_text('Resume validation against job requirements')
                        expect(panel).to_contain_text('Novel expertise')
                        expect(panel).to_contain_text('Not demonstrated')
                        expect(panel).not_to_contain_text('target 80%')
                        self.assertEqual(validate.call_count, 1)
                        panel.get_by_label('I reviewed both AI documents', exact=False).check()
                        panel.get_by_role('button', name='Confirm AI revision for export').click()
                        for kind, label in (('resume', 'resume'), ('cover_letter', 'cover letter')):
                            for format in ('DOCX', 'PDF'):
                                if format == 'PDF' and os.environ.get('CAREER_AGENT_TEST_PDF') != '1':
                                    continue
                                with page.expect_download() as exported:
                                    panel.get_by_role('button', name='Download reviewed AI ' + label + ' ' + format, exact=True).click()
                                file = Path(exported.value.path())
                                if format == 'DOCX':
                                    output = '\n'.join(p.text for p in Document(file).paragraphs)
                                else:
                                    from pypdf import PdfReader
                                    output = '\n'.join(p.extract_text() for p in PdfReader(file).pages)
                                self.assertIn('Delivered an AI program' if kind == 'resume' else 'My experience delivering an AI program', output)
                                self.assertNotIn('Private fictional evidence', output)
                                self.assertNotIn('experience-01', output)
                        with page.expect_download() as received:
                            panel.get_by_role('button', name='Download reviewed AI cover letter text').click()
                        text = Path(received.value.path()).read_text(encoding='utf-8')
                        self.assertIn('Dear Hiring Team', text)
                        self.assertIn('My experience delivering an AI program', text)
                        page.reload()
                        page.locator('#nav-jobs').click(); page.locator('#job-list button').first.click()
                        page.get_by_role('tab', name='Application').click()
                        panel = page.locator('.document-revision-studio')
                        panel.locator('summary').first.click()
                        panel.get_by_role('button', name='Load models and saved revisions').click()
                        expect(panel.get_by_role('button', name='Download reviewed AI resume text')).to_be_visible()
                        self.assertEqual(complete.call_count, 2)
                        panel.get_by_role('button', name='Validate resume against job', exact=True).click()
                        expect(panel).to_contain_text('confirm sharing before validation or repair')
                        self.assertEqual(validate.call_count, 1)
                        def unsupported(snapshot, revision, route_name, **kwargs):
                            result = fictional_validation(snapshot, revision, route_name)
                            result['export_allowed'] = False
                            result['claims']['passages'][0].update(status='unsupported', reason='Fictional skill-to-project association is unsupported.')
                            return result
                        validate.side_effect = unsupported
                        panel.get_by_label('I consent to sharing', exact=False).check()
                        panel.get_by_role('button', name='Validate resume against job', exact=True).click()
                        expect(panel).to_contain_text('Fictional skill-to-project association is unsupported.')
                        expect(panel.get_by_role('button', name='Confirm AI revision for export')).to_be_disabled()
                        expect(panel.get_by_role('button', name='Download reviewed AI resume text')).to_have_count(0)
                        validate.side_effect = fictional_validation
                        panel.get_by_label('I consent to sharing', exact=False).check()
                        panel.get_by_role('button', name='Repair wording and add verified evidence', exact=True).click()
                        expect(panel).to_contain_text('Repaired revision saved.')
                        expect(panel.get_by_role('button', name='Confirm AI revision for export')).to_be_disabled()
                        expect(panel.get_by_role('button', name='Download reviewed AI resume text')).to_have_count(0)
                        self.assertEqual(validate.call_count, 3)
                        page.set_viewport_size({'width': 390, 'height': 844})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

