"""Offline résumé export fidelity and actual optional Chromium rendering."""
import io
import os
import unittest

from career_agent.application import packet_fingerprint
from career_agent.resume_pdf import resume_html, resume_pdf


def sample_packet(text):
    packet = {"version": 2, "job_id": "a" * 16, "resume_markdown": text,
              "cover_note": "PRIVATE COVER CONTENT", "private_source": "PRIVATE EVIDENCE"}
    packet["packet_sha256"] = packet_fingerprint(packet)
    return packet


class ResumeHTMLTests(unittest.TestCase):
    def test_text_is_escaped_and_private_fields_excluded(self):
        html = resume_html(sample_packet('# Fictional Candidate\n## Skills\n- A & B <script>alert(1)</script>\n'))
        self.assertIn("A &amp; B &lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("PRIVATE EVIDENCE", html)
        self.assertNotIn("PRIVATE COVER", html)
        self.assertNotIn("url(", html)
        self.assertIn("font-variant-ligatures: none", html)

    def test_tampered_packet_is_rejected(self):
        packet = sample_packet("# Fictional Candidate\n")
        packet["resume_markdown"] += "unreviewed change"
        with self.assertRaises(ValueError):
            resume_html(packet)


@unittest.skipUnless(os.environ.get("CAREER_AGENT_TEST_PDF") == "1", "Enable PDF integration tests with installed Chromium")
class ResumePDFTests(unittest.TestCase):
    def test_pdf_contains_selectable_text_and_all_long_document_facts(self):
        from pypdf import PdfReader
        statements = [f"Fictional achievement {i:03d}: verification of financial workflows and software delivery." for i in range(80)]
        packet = sample_packet("# Fictional Candidate\nfictional@example.invalid\nAI Program Lead\n## Relevant Experience\n" + "\n".join("- " + s for s in statements))
        content = resume_pdf(packet)
        self.assertTrue(content.startswith(b"%PDF-"))
        reader = PdfReader(io.BytesIO(content))
        self.assertGreater(len(reader.pages), 1)
        text = " ".join(" ".join(page.extract_text().split()) for page in reader.pages)
        self.assertIn("Fictional Candidate", text)
        self.assertIn("fictional@example.invalid", text)
        for statement in statements:
            self.assertIn(statement, text)
        self.assertNotIn("PRIVATE EVIDENCE", text)
        self.assertNotIn("PRIVATE COVER", text)
