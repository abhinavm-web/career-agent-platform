"""Regression tests for three issues reported in PR #2's Copilot review.

These tests use only fictional data and temporary local paths.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career_agent.application import build_packet, save_packet
from career_agent.candidate import FactBank
from career_agent.graph_review import _decode
from career_agent.models import CandidateProfile, Job
from career_agent.sponsor_evidence import FilingEvidence, load_filing_evidence


def packet():
    job = Job.from_mapping({
        "company": "Example Industries",
        "title": "AI Manager",
        "job_url": "https://example.invalid/roles/example",
    })
    bank = FactBank.parse({
        "name": "Fictional Candidate",
        "facts": [{
            "id": "fact-01",
            "section": "experience",
            "statement": "Built a fictional automation prototype for an example company.",
            "keywords": ["automation"],
            "source_note": "Fictional source note",
            "verified_by_candidate": True,
        }],
    })
    return build_packet(
        job,
        CandidateProfile(
            target_titles=("AI Manager",),
            skills=("automation",),
            preferred_locations=(),
        ),
        bank,
    )


def graph_state(records):
    return {
        "job": {
            "company": "Example Industries",
            "title": "AI Manager",
            "job_url": "https://example.invalid/roles/example",
        },
        "preferences": {
            "target_titles": ["AI Manager"],
            "skills": [],
            "preferred_locations": [],
            "allow_remote": True,
        },
        "fact_bank": {
            "name": "Fictional Candidate",
            "facts": [{
                "id": "fact-01",
                "section": "experience",
                "statement": "Built a fictional automation prototype for an example company.",
                "keywords": [],
                "source_note": "Fictional source note",
                "verified_by_candidate": True,
            }],
        },
        "filing_records": records,
    }


def evidence():
    return {
        "employer": "Example Industries",
        "fiscal_year": 2025,
        "source_url": "https://example.invalid/evidence",
        "source_type": "user-supplied",
        "case_status": "",
    }


class PacketPathSafetyTests(unittest.TestCase):
    def test_parent_directory_symlink_rejected_without_outside_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            target = base / "packets"
            try:
                target.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Cannot create directory symlink on this platform")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                save_packet(packet(), target / "packet.json")
            self.assertFalse((outside / "packet.json").exists())

    def test_nested_directory_symlink_rejected_without_outside_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            inner_link = base / "parent"
            try:
                inner_link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Cannot create directory symlink on this platform")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                save_packet(packet(), inner_link / "nested" / "packet.json")
            self.assertFalse((outside / "nested").exists())

    def test_symlinked_target_rejected_and_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / "outside.json"
            original.write_text("retain this private file")
            link = base / "packet.json"
            try:
                link.symlink_to(original)
            except (OSError, NotImplementedError):
                self.skipTest("Cannot create file symlink on this platform")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                save_packet(packet(), link)
            self.assertEqual(original.read_text(), "retain this private file")

    def test_normal_packet_writes_and_can_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "private" / "nested" / "packet.json"
            save_packet(packet(), destination)
            self.assertTrue(destination.is_file())
            save_packet(packet(), destination)
            self.assertEqual(json.loads(destination.read_text())["status"], "awaiting_human_review")


class FilingStateValidationTests(unittest.TestCase):
    def test_valid_filing_record_roundtrips(self):
        _, _, _, decoded = _decode(graph_state([evidence()]))
        self.assertEqual(decoded, (FilingEvidence(**evidence()),))

    def test_bad_filing_collection_type_raises_value_error(self):
        for records in (None, {}, "not a list", 42, [None], ["text"], [[1, 2]]):
            with self.subTest(records=records):
                with self.assertRaisesRegex(ValueError, "filing_records"):
                    _decode(graph_state(records))

    def test_missing_or_unexpected_fields_raise_value_error(self):
        incomplete = evidence()
        del incomplete["employer"]
        extra = {**evidence(), "secret": "unexpected"}
        for record in (incomplete, extra):
            with self.subTest(record=record):
                with self.assertRaisesRegex(ValueError, "filing_records"):
                    _decode(graph_state([record]))

    def test_bad_year_and_url_values_raise_value_error(self):
        for bad in (
            {**evidence(), "fiscal_year": "2025"},
            {**evidence(), "fiscal_year": True},
            {**evidence(), "fiscal_year": 99},
            {**evidence(), "source_url": "javascript:alert(1)"},
            {**evidence(), "source_type": 99},
            {**evidence(), "case_status": 42},
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "filing_records"):
                    _decode(graph_state([bad]))


class CSVYearHeaderTests(unittest.TestCase):
    def test_missing_year_header_fails_before_parsing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filings.csv"
            path.write_text(
                "employer,source_url\nExample Industries,https://example.invalid/evidence\n"
            )
            with self.assertRaisesRegex(ValueError, "fiscal_year or record_year"):
                load_filing_evidence(path)

    def test_record_year_alias_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filings.csv"
            path.write_text(
                "employer,record_year,source_url\n"
                "Example Industries,2025,https://example.invalid/evidence\n"
            )
            rows = load_filing_evidence(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].fiscal_year, 2025)


if __name__ == "__main__":
    unittest.main()
