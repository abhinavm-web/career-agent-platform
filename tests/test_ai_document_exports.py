"""Fictional reviewed exports: literal text, privacy, formats and stale-source races."""
import io
import json
import os
import unittest
from unittest.mock import patch

import test_dashboard
from test_document_revisions import ROUTE, OUTPUT
from career_agent import ai
from career_agent.ai_document_exports import document_docx, document_html, document_pdf
from career_agent.storage import Store


class ExportRenderingTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'name': 'Fictional Candidate', 'headline': 'Engineering leader',
                         'contact': 'fictional@example.invalid', 'private_note': 'PRIVATE NOTE'}
        self.text = 'Led an evaluation, projecting 12% savings; not deployed. <script>literal</script>\nSecond line.'
        self.revision = {'document': {'resume': [
            {'section': 'summary', 'text': 'A fictional career summary.', 'fact_ids': ['fact-01']},
            {'section': 'experience', 'text': self.text, 'fact_ids': ['fact-01']}],
            'cover_letter': [{'text': self.text, 'fact_ids': ['fact-01']}]},
            'sources': {'fact-01': 'PRIVATE SOURCE'}, 'instructions': 'PRIVATE INSTRUCTIONS'}

    def test_html_and_word_preserve_text_without_private_metadata_or_active_content(self):
        from docx import Document
        for kind in ('resume', 'cover_letter'):
            html = document_html(self.revision, kind, self.snapshot)
            self.assertIn('&lt;script&gt;literal&lt;/script&gt;', html)
            self.assertNotIn('<script>', html)
            self.assertNotIn('PRIVATE', html)
            self.assertNotIn('fact-01', html)
            document = Document(io.BytesIO(document_docx(self.revision, kind, self.snapshot)))
            text = '\n'.join(p.text for p in document.paragraphs)
            self.assertIn(self.text, text)
            self.assertNotIn('PRIVATE', text)
            self.assertNotIn('fact-01', text)
            self.assertEqual(document.core_properties.author, '')
            self.assertFalse(any(rel.is_external for rel in document.part.rels.values()))
            if kind == 'cover_letter':
                self.assertIn('Dear Hiring Team,', text)
                self.assertIn('Sincerely,\nFictional Candidate', text)

    def test_unsupported_characters_fail_cleanly(self):
        self.snapshot['name'] = 'Invalid\x00Name'
        for renderer in (document_html, document_docx):
            with self.assertRaises(ValueError): renderer(self.revision, 'resume', self.snapshot)

    @unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_PDF') == '1', 'Enable Chromium/PDF tests')
    def test_real_pdfs_keep_all_text_and_have_no_external_actions(self):
        from pypdf import PdfReader
        for kind in ('resume', 'cover_letter'):
            reader = PdfReader(io.BytesIO(document_pdf(self.revision, kind, self.snapshot)))
            text = ' '.join(' '.join(page.extract_text().split()) for page in reader.pages)
            self.assertIn('projecting 12% savings; not deployed.', text)
            self.assertIn('<script>literal</script> Second line.', text)
            self.assertNotIn('PRIVATE', text)
            self.assertNotIn('/OpenAction', reader.trailer['/Root'])
            self.assertEqual(len(reader.pages), 1)


class ExportAPITests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    call = test_dashboard.DashboardTests.call

    def create_revision(self):
        path = '/api/jobs/' + self.job.id + '/document-revisions'
        request = {'route': 'claude', 'route_fingerprint': ai.route_fingerprint('claude', ROUTE),
                   'instructions': 'Keep qualifiers.', 'parent_id': 0, 'acknowledged': True}
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)):
            result = self.call('POST', path, request)
        self.assertEqual(result.status_code, 200, result.text)
        return path + '/' + str(result.json()['revisions'][0]['id'])

    def test_all_formats_require_current_review_and_export_without_inference(self):
        path = self.create_revision()
        for kind in ('resume', 'cover_letter'):
            for format in ('txt', 'pdf', 'docx'):
                self.assertEqual(self.call('GET', path + '/' + kind + '.' + format).status_code, 422)
        self.call('POST', path + '/review', {'acknowledged': True})
        with patch('career_agent.ai.complete') as model, patch('career_agent.ai_document_exports.document_pdf', return_value=b'%PDF-fictional'):
            for kind in ('resume', 'cover_letter'):
                for format in ('txt', 'pdf', 'docx'):
                    response = self.call('GET', path + '/' + kind + '.' + format)
                    self.assertEqual(response.status_code, 200, response.text[:150] if format == 'txt' else response.status_code)
                    self.assertIn('.' + format, response.headers['content-disposition'])
            model.assert_not_called()
        self.assertEqual(self.call('GET', path + '/resume.html').status_code, 422)
        self.assertEqual(self.call('GET', path + '/private.pdf').status_code, 422)
        with Store(self.db) as store: store.correct_description(self.job.id, 'Changed posting.', 0, 'Fictional source')
        for format in ('txt', 'pdf', 'docx'):
            self.assertEqual(self.call('GET', path + '/resume.' + format).status_code, 422)

    def test_source_change_during_rendering_never_releases_stale_bytes(self):
        path = self.create_revision()
        self.call('POST', path + '/review', {'acknowledged': True})
        def changed(*args):
            with Store(self.db) as store: store.correct_description(self.job.id, 'Changed while rendering.', 0, 'Fictional source')
            return b'%PDF-stale-content'
        with patch('career_agent.ai_document_exports.document_pdf', side_effect=changed):
            response = self.call('GET', path + '/resume.pdf')
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'%PDF-stale-content', response.content)
