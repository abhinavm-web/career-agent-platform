"""Candidate-grounded drafts, provenance, CLI and graph review safety tests."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from career_agent.application import build_packet, load_packet, save_packet, validate_packet
from career_agent.candidate import FactBank
from career_agent.cli import main
from career_agent.models import CandidateProfile, Job
from career_agent.sponsor_evidence import (
    FilingEvidence, employer_key, load_filing_evidence, sponsorship_report,
)
from career_agent.storage import Store

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def fictional_facts() -> dict:
    return {
        "name": "Demo Candidate",
        "headline": "AI Program Delivery",
        "facts": [
            {
                "id": "delivery-01",
                "section": "experience",
                "statement": "Delivered a fictional AWS automation pilot with a documented review process.",
                "keywords": ["AWS", "AI"],
                "source_note": "Fictional internal project record.",
                "verified_by_candidate": True,
            },
            {
                "id": "cert-01",
                "section": "certifications",
                "statement": "Completed a fictional cloud training program.",
                "keywords": ["cloud"],
                "source_note": "Fictional training record.",
                "verified_by_candidate": True,
            },
            {
                "id": "projects-01",
                "section": "projects",
                "statement": "Created a fictional Python project prototype.",
                "keywords": ["Python"],
                "source_note": "Fictional project folder.",
                "verified_by_candidate": True,
            },
        ],
    }


def fictional_job() -> Job:
    return Job.from_mapping({
        "company": "Example Industries",
        "title": "AI Program Manager",
        "location": "Remote, USA",
        "job_url": "https://example.com/jobs/example-ai-manager",
        "description": "We need AWS and Python. Visa sponsorship is available.",
        "is_remote": "true",
    })


def fictional_preferences() -> CandidateProfile:
    return CandidateProfile(
        target_titles=("AI Program Manager",),
        skills=("AWS", "Python"),
        preferred_locations=(),
        allow_remote=True,
    )


def fictional_evidence() -> tuple[FilingEvidence, ...]:
    return (
        FilingEvidence(
            employer="Example Industries",
            fiscal_year=2025,
            source_url="https://www.dol.gov/agencies/eta/foreign-labor/performance",
            source_type="DOL LCA disclosure (candidate-supplied; not independently verified)",
        ),
    )


class FactBankTests(unittest.TestCase):
    def test_only_candidate_confirmed_facts_allowed(self):
        raw = fictional_facts()
        raw["facts"][0]["verified_by_candidate"] = False
        with self.assertRaisesRegex(ValueError, "verified_by_candidate"):
            FactBank.parse(raw)

    def test_duplicate_fact_ids_rejected(self):
        raw = fictional_facts()
        raw["facts"][1]["id"] = raw["facts"][0]["id"]
        with self.assertRaisesRegex(ValueError, "unique"):
            FactBank.parse(raw)

    def test_private_notes_not_returned_as_claims(self):
        bank = FactBank.parse(fictional_facts())
        self.assertIn("AWS automation", bank.public_claims()["delivery-01"])
        self.assertNotIn("internal project record", bank.public_claims()["delivery-01"])

    def test_empty_or_invalid_keywords_rejected(self):
        raw = fictional_facts()
        raw["facts"][0]["keywords"] = ["", 123]
        with self.assertRaisesRegex(ValueError, "keywords"):
            FactBank.parse(raw)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.job = fictional_job()
        self.bank = FactBank.parse(fictional_facts())
        self.packet = build_packet(
            self.job, fictional_preferences(), self.bank, fictional_evidence()
        )

    def test_packet_has_exact_supported_claims_no_inventions(self):
        resume = self.packet["resume_markdown"]
        self.assertIn("Delivered a fictional AWS automation pilot", resume)
        self.assertIn("Created a fictional Python project prototype", resume)
        self.assertNotIn("10 years of experience", resume)
        self.assertEqual(len(self.packet["selected_fact_ids"]), 3)
        self.assertFalse(self.packet["submission_sent"])
        self.assertEqual(self.packet["status"], "awaiting_human_review")

    def test_candidate_skill_absent_from_posting_is_not_a_qualification_gap(self):
        rust_job = Job.from_mapping({
            "company": "Example Industries",
            "title": "Rust Engineer",
            "location": "Remote, USA",
            "job_url": "https://example.com/jobs/rust",
            "description": "Rust is required for this role.",
            "is_remote": True,
        })
        candidate = CandidateProfile(
            target_titles=("Rust Engineer",),
            skills=("Python",),
            preferred_locations=(),
            allow_remote=True,
        )
        packet = build_packet(rust_job, candidate, self.bank)
        self.assertEqual(packet["candidate_skills_not_mentioned_in_posting"], ["Python"])
        self.assertNotIn("reported_skill_gaps", packet)
        self.assertFalse(packet["qualification_gaps_assessed"])
        self.assertNotIn("Rust", packet["candidate_skills_not_mentioned_in_posting"])
        self.assertEqual(validate_packet(packet), packet["packet_sha256"])

    def test_older_packet_schema_requires_regeneration(self):
        previous = dict(self.packet)
        previous["version"] = 1
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_packet(previous)

    def test_packet_provenance_does_not_disclose_private_notes(self):
        content = json.dumps(self.packet)
        self.assertNotIn("Fictional internal project record", content)
        self.assertIn("delivery-01", content)

    def test_packet_evidence_tracks_verified_selection_and_omissions(self):
        raw = fictional_facts()
        for i in range(12):
            raw["facts"].append({"id": f"extra-{i:02d}", "section": "experience",
                "statement": f"Fictional verifiable achievement number {i}.", "keywords": [],
                "source_note": "Private supporting record", "verified_by_candidate": True})
        bank = FactBank.parse(raw)
        packet = build_packet(self.job, fictional_preferences(), bank)
        self.assertEqual([fact["id"] for fact in packet["fact_evidence"]], packet["selected_fact_ids"])
        self.assertEqual(len(packet["fact_evidence"]), 12)
        self.assertEqual(len(packet["omitted_fact_ids"]), 3)
        self.assertEqual(set(packet["selected_fact_ids"]) | set(packet["omitted_fact_ids"]),
                         {fact.fact_id for fact in bank.facts})
        self.assertNotIn("Private supporting record", json.dumps(packet))
        self.assertTrue(any("Python" in fact["matched_keywords"] for fact in packet["fact_evidence"]))
        packet["fact_evidence"][0]["statement"] = "Unreviewed changed statement"
        with self.assertRaisesRegex(ValueError, "changed"):
            validate_packet(packet)

    def test_tampering_requires_fresh_review(self):
        self.packet["resume_markdown"] += "\n- Invented success.\n"
        with self.assertRaisesRegex(ValueError, "changed"):
            validate_packet(self.packet)

    def test_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_packet(self.packet, Path(tmp) / "packet.json")
            loaded = load_packet(path)
            self.assertEqual(loaded["packet_sha256"], self.packet["packet_sha256"])

    def test_evidence_is_not_a_job_sponsorship_promise(self):
        info = self.packet["sponsorship"]
        self.assertEqual(info["status"], "posting_claims_sponsorship")
        self.assertEqual(info["history_verification"], "user_supplied_not_independently_verified")
        self.assertIn("not a job-level sponsorship offer", info["disclaimer"])


class SponsorshipEvidenceTests(unittest.TestCase):
    def test_canonical_employer_exact_no_fuzzy_subsidiary_match(self):
        self.assertEqual(employer_key("  EXAMPLE   Industries "), "example industries")
        report = sponsorship_report(
            Job.from_mapping({
                "company": "Example Industries Subsidiary",
                "title": "AI Manager",
                "job_url": "https://example.org/job",
            }),
            fictional_evidence(),
        )
        self.assertEqual(report["historical_records"], [])
        self.assertEqual(report["status"], "unknown")

    def test_csv_import_requires_year_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filings.csv"
            path.write_text(
                "employer,fiscal_year,source_url,source_type\n"
                "Example Industries,2025,https://www.dol.gov/example,DOL LCA\n"
            )
            result = load_filing_evidence(path)
            self.assertEqual(result[0].fiscal_year, 2025)
            path.write_text(
                "employer,fiscal_year,source_url\n"
                "Example Industries,2025,not-a-url\n"
            )
            with self.assertRaisesRegex(ValueError, "source_url"):
                load_filing_evidence(path)


class CLIIntegrationTests(unittest.TestCase):
    def test_draft_creates_local_packet_not_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / "jobs.sqlite3"
            facts_path = base / "candidate.json"
            prefs_path = base / "preferences.json"
            packet_path = base / "private" / "packet.json"
            facts_path.write_text(json.dumps(fictional_facts()))
            prefs_path.write_text(json.dumps(asdict(fictional_preferences())))
            job = fictional_job()
            with Store(db) as store:
                store.add_jobs([job])
            result = io.StringIO()
            with redirect_stdout(result):
                code = main([
                    "--db", str(db), "draft", job.id,
                    "--profile", str(prefs_path), "--facts", str(facts_path),
                    "--output", str(packet_path),
                ])
            self.assertEqual(code, 0)
            self.assertTrue(packet_path.exists())
            self.assertFalse(load_packet(packet_path)["submission_sent"])
            self.assertEqual(json.loads(result.getvalue())["status"], "awaiting_human_review")
            with Store(db) as store:
                self.assertEqual(store.stage_of(job.id), "discovered")


@unittest.skipUnless(HAS_LANGGRAPH, "Install optional graph extra for real LangGraph tests")
class GraphTests(unittest.TestCase):
    def setUp(self):
        self.packet = build_packet(
            fictional_job(), fictional_preferences(),
            FactBank.parse(fictional_facts()), fictional_evidence(),
        )

    def test_interrupt_then_explicit_approval_without_sending(self):
        from career_agent.graph_review import build_review_graph, resume_review
        graph = build_review_graph()
        tid = str(uuid.uuid4())
        paused = graph.invoke(
            {"packet": self.packet}, config={"configurable": {"thread_id": tid}}
        )
        self.assertIn("__interrupt__", paused)
        self.assertEqual(resume_review(graph, self.packet, True, tid), "approved_for_manual_submission")
        self.assertFalse(self.packet["submission_sent"])

    def test_reject_route(self):
        from career_agent.graph_review import build_review_graph, resume_review
        graph = build_review_graph()
        tid = str(uuid.uuid4())
        graph.invoke({"packet": self.packet}, config={"configurable": {"thread_id": tid}})
        self.assertEqual(resume_review(graph, self.packet, False, tid), "rejected_by_reviewer")

    def test_full_graph_analyzes_drafts_and_interrupts(self):
        from career_agent.graph_review import build_career_graph, resume_review
        graph = build_career_graph()
        tid = str(uuid.uuid4())
        state = {
            "job": asdict(fictional_job()),
            "preferences": asdict(fictional_preferences()),
            "fact_bank": fictional_facts(),
            "filing_records": [asdict(item) for item in fictional_evidence()],
        }
        paused = graph.invoke(state, config={"configurable": {"thread_id": tid}})
        self.assertIn("__interrupt__", paused)
        self.assertEqual(paused["analysis"]["sponsorship"]["status"], "posting_claims_sponsorship")
        self.assertEqual(paused["packet"]["job_id"], fictional_job().id)
        self.assertEqual(
            resume_review(graph, paused["packet"], True, tid),
            "approved_for_manual_submission",
        )

    def test_stale_packet_fingerprint_rejected(self):
        from langgraph.types import Command
        from career_agent.graph_review import build_review_graph
        graph = build_review_graph()
        tid = str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        graph.invoke({"packet": self.packet}, config=config)
        with self.assertRaises(ValueError):
            graph.invoke(Command(resume={
                "reviewed": True, "approved": True,
                "packet_sha256": "0" * 64,
            }), config=config)


if __name__ == "__main__":
    unittest.main()
