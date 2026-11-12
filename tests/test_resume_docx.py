"""Reviewed Word document export uses only saved packet text."""
import io
import unittest

from career_agent.application import build_packet, packet_fingerprint
from career_agent.candidate import FactBank
from career_agent.models import CandidateProfile
from career_agent.resume_docx import resume_docx
from test_dashboard import fake_fact_bank, fake_job


class ResumeDOCXTests(unittest.TestCase):
    def setUp(self):
        self.packet = build_packet(fake_job(), CandidateProfile((), (), ()), FactBank.parse(fake_fact_bank()))

    def test_word_document_has_fingerprint_bound_claims_without_private_source(self):
        from docx import Document
        result = resume_docx(self.packet)
        document = Document(io.BytesIO(result))
        content = '\n'.join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn('Fictional Candidate', content)
        self.assertIn('Delivered a fictional AI program involving Python and AWS.', content)
        self.assertNotIn('Private fictional evidence', content)
        self.assertFalse(any(rel.is_external for rel in document.part.rels.values()))
        self.assertEqual(document.core_properties.author, '')

    def test_contact_line_is_in_exported_word_document(self):
        from docx import Document
        data = fake_fact_bank(); data['resume_contact'] = 'fictional@example.invalid · +1 555 0100'
        packet = build_packet(fake_job(), CandidateProfile((), (), ()), FactBank.parse(data))
        document = Document(io.BytesIO(resume_docx(packet)))
        self.assertIn(data['resume_contact'], '\n'.join(p.text for p in document.paragraphs))

    def test_multiline_candidate_text_stays_one_literal_fact(self):
        from docx import Document
        data = fake_fact_bank()
        data['facts'][0]['statement'] = 'Built a fictional system.\n## Not a section\n- Still the same fact'
        packet = build_packet(fake_job(), CandidateProfile((), (), ()), FactBank.parse(data))
        document = Document(io.BytesIO(resume_docx(packet)))
        matches = [p for p in document.paragraphs if p.text == data['facts'][0]['statement']]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].style.name, 'List Bullet')
        self.assertFalse(any(p.text == 'Not a section' for p in document.paragraphs))
        self.assertFalse(any(p.text == 'Still the same fact' for p in document.paragraphs))

    def test_block_mismatch_rejected_even_if_packet_is_rehashed(self):
        self.packet['resume_blocks'][-1]['text'] = 'Changed fictional claim.'
        self.packet['packet_sha256'] = packet_fingerprint(self.packet)
        with self.assertRaisesRegex(ValueError, 'does not match the reviewed text'):
            resume_docx(self.packet)

    def test_legacy_packet_without_blocks_uses_existing_rendering(self):
        from docx import Document
        self.packet.pop('resume_blocks')
        self.packet['packet_sha256'] = packet_fingerprint(self.packet)
        doc = Document(io.BytesIO(resume_docx(self.packet)))
        self.assertIn('Delivered a fictional AI program involving Python and AWS.', [p.text for p in doc.paragraphs])

    def test_word_export_rejects_tampered_packet(self):
        self.packet['resume_markdown'] += '\nInvented qualification'
        with self.assertRaises(ValueError):
            resume_docx(self.packet)

    def test_unsupported_xml_characters_fail_with_an_actionable_error(self):
        self.packet['resume_markdown'] += '\n\x01'
        self.packet['packet_sha256'] = packet_fingerprint(self.packet)
        with self.assertRaisesRegex(ValueError, 'unsupported by Word'):
            resume_docx(self.packet)
