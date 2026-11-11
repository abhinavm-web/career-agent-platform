"""Fictional local résumé extraction; no candidate facts are created or verified."""
import base64
import io
import unittest
import zipfile

from career_agent.resume_import import extract_resume, MAX_BYTES


def encoded(data):
    return base64.b64encode(data).decode('ascii')


class ResumeImportTests(unittest.TestCase):
    def test_text_and_word_extract_verbatim_text(self):
        self.assertEqual(extract_resume('resume.txt', encoded('Fictional engineer\nBuilt tools'.encode())),
                         'Fictional engineer\nBuilt tools')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml',
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Fictional candidate</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Built tools</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_resume('resume.docx', encoded(output.getvalue())), 'Fictional candidate\nBuilt tools')

    def test_invalid_and_unsupported_content_is_rejected(self):
        for name, value in [('resume.txt', '!!!!'), ('resume.txt', encoded(b'\xff')),
                            ('resume.txt', encoded(b'  ')), ('resume.pdf', encoded(b'not pdf')),
                            ('resume.docx', encoded(b'not zip')), ('resume.doc', encoded(b'anything')),
                            ('resume.txt', encoded(b'x' * (MAX_BYTES + 1)))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                extract_resume(name, value)

    def test_pdf_blank_page_does_not_invent_text(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest('Install the resume extra')
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        data = io.BytesIO(); writer.write(data)
        with self.assertRaisesRegex(ValueError, 'No text found'):
            extract_resume('resume.pdf', encoded(data.getvalue()))

    def test_pdf_text_is_extracted(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        except ImportError:
            self.skipTest('Install the resume extra')
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 100 Td (Fictional engineer) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        data = io.BytesIO(); writer.write(data)
        self.assertEqual(extract_resume('resume.pdf', encoded(data.getvalue())), 'Fictional engineer')
