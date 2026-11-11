"""Offline regressions using fictional data only."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from career_agent.cli import main
from career_agent.models import CandidateProfile, Job, canonical_url
from career_agent.scoring import score_job
from career_agent.sponsorship import assess_sponsorship, load_historical_evidence
from career_agent.storage import Store


def sample_job(description: str = "", url: str = "https://example.com/job?id=123&utm_source=share") -> Job:
    return Job.from_mapping({
        "company": "Example Industries",
        "title": "AI Transformation Manager",
        "location": "Knoxville, TN",
        "job_url": url,
        "description": description,
    })


class ModelTests(unittest.TestCase):
    def test_normalization_drops_tracking_but_keeps_job_id(self):
        self.assertEqual(
            canonical_url("https://EXAMPLE.com/job?utm_source=x&id=123"),
            "https://example.com/job?id=123",
        )

    def test_different_job_ids_do_not_collide(self):
        self.assertNotEqual(sample_job().id, sample_job(url="https://example.com/job?id=124").id)

    def test_unsafe_url_is_rejected(self):
        for value in ("javascript:alert(1)", "https://name:password@example.com/job"):
            with self.assertRaises(ValueError):
                canonical_url(value)

    def test_invalid_profile_types_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.json"
            profile_path.write_text(json.dumps({"skills": "AWS"}))
            with self.assertRaises(ValueError):
                CandidateProfile.load(profile_path)


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = CandidateProfile(
            target_titles=("AI Transformation Manager",),
            skills=("AWS", "Python"),
            preferred_locations=("Knoxville",),
            allow_remote=False,
        )

    def test_explainable_full_match(self):
        result = score_job(sample_job("AWS and Python"), self.profile)
        self.assertEqual(result.score, 100)
        self.assertEqual(set(result.matched_skills), {"AWS", "Python"})

    def test_missing_skills_are_not_invented(self):
        result = score_job(sample_job("Spreadsheets"), self.profile)
        self.assertEqual(result.score, 50)
        self.assertEqual(set(result.candidate_skills_not_mentioned_in_posting), {"AWS", "Python"})
        self.assertIn("does not establish a qualification gap", ' '.join(result.reasons))

    def test_explanation_names_matching_skills_and_unknown_location(self):
        from dataclasses import replace
        result = score_job(replace(sample_job("Python experience"), location="", remote=False), self.profile)
        self.assertIn("Skills found in posting: Python", result.reasons)
        self.assertIn("location is unspecified", ' '.join(result.reasons))

    def test_location_preference_does_not_match_inside_city_name(self):
        from dataclasses import replace
        preferences = replace(self.profile, preferred_locations=("CA",))
        chicago = score_job(replace(sample_job("AWS and Python"), location="Chicago, IL"), preferences)
        oakland = score_job(replace(sample_job("AWS and Python"), location="Oakland, CA"), preferences)
        self.assertEqual(chicago.score, 90)
        self.assertEqual(oakland.score, 100)
        self.assertIn('verify the posting', ' '.join(chicago.reasons))

    def test_c_language_does_not_match_cpp_or_csharp(self):
        from dataclasses import replace
        preferences = replace(self.profile, skills=("C",))
        for text in ("C++ development", "C# development"):
            with self.subTest(text=text):
                self.assertEqual(score_job(sample_job(text), preferences).matched_skills, ())
        self.assertEqual(score_job(sample_job("C and C++"), preferences).matched_skills, ("C",))

    def test_remote_match_does_not_claim_geographic_eligibility(self):
        from dataclasses import replace
        job = replace(sample_job("AWS and Python"), location="Remote - Canada only", remote=True)
        result = score_job(job, replace(self.profile, allow_remote=True))
        self.assertIn('geographic eligibility still needs verification', ' '.join(result.reasons))
        self.assertIn('does not assess qualification', ' '.join(result.reasons))


class SponsorshipTests(unittest.TestCase):
    def test_exclusion_overrides_historical_data(self):
        result = assess_sponsorship(sample_job("No visa sponsorship."), {"example industries"})
        self.assertEqual(result.status, "explicit_exclusion")

    def test_historical_not_promised(self):
        result = assess_sponsorship(sample_job("We use Python"), {"example industries"})
        self.assertEqual(result.status, "historical_only")

    def test_no_evidence_is_unknown(self):
        self.assertEqual(assess_sponsorship(sample_job()).status, "unknown")

    def test_positive_posting_still_needs_verification(self):
        result = assess_sponsorship(sample_job("Visa sponsorship is available."))
        self.assertEqual(result.status, "posting_claims_sponsorship")
        self.assertIn("confirmation", result.reason)

    def test_historical_data_requires_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.csv"
            path.write_text(
                "employer,source_url\nExample Industries,\nOther Example,https://example.org/filing\n"
            )
            self.assertEqual(load_historical_evidence(path), {"other example"})


class StorageTests(unittest.TestCase):
    def test_dedupe_review_and_confirmation_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "local.sqlite3") as store:
                job = sample_job()
                self.assertEqual(store.add_jobs([job, job]), 1)
                self.assertEqual(store.stage_of(job.id), "discovered")
                with self.assertRaises(ValueError):
                    store.confirm(job.id, "receipt-123", attested=True)
                store.stage(job.id)
                with self.assertRaises(ValueError):
                    store.confirm(job.id, "receipt-123", attested=False)
                store.confirm(job.id, "receipt-123", attested=True)
                self.assertEqual(store.stage_of(job.id), "submitted")
                with self.assertRaises(ValueError):
                    store.stage(job.id)

    def test_cli_import_and_list_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            csv = base / "sample.csv"
            csv.write_text(
                "company,title,location,job_url,description,is_remote\n"
                "Example Industries,AI Lead,Remote,https://example.com/jobs/42,AWS,true\n"
            )
            db = str(base / "jobs.sqlite3")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--db", db, "ingest", str(csv)]), 0)
            self.assertEqual(json.loads(output.getvalue())["new"], 1)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--db", db, "list"]), 0)
            self.assertEqual(json.loads(output.getvalue())[0]["stage"], "discovered")

    def test_cli_requires_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "jobs.sqlite3")
            job = sample_job()
            with Store(db) as store:
                store.add_jobs([job])
                store.stage(job.id)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(["--db", db, "confirm", job.id, "--receipt", "ref-abc"]),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
