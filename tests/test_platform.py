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
    def test_missing_scalars_fall_back_without_boolean_coercion(self):
        class MissingScalar:
            def __bool__(self):
                raise TypeError('ambiguous missing scalar')
            def __str__(self):
                return '<NA>'
        missing = MissingScalar()
        job = Job.from_mapping({'company': missing, 'company_name': 'Example',
                                'title': float('nan'), 'job_title': 'Engineer',
                                'job_url': missing, 'url': 'https://example.com/job/1',
                                'site': missing, 'source': 'indeed',
                                'description': missing, 'posted_date_status': missing})
        self.assertEqual((job.company, job.title, job.source), ('Example', 'Engineer', 'indeed'))
        self.assertEqual(job.url, 'https://example.com/job/1')
        self.assertEqual(job.description, '')
        self.assertEqual(job.posted_date_status, 'missing')

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

    def test_duplicate_skill_spellings_do_not_inflate_score(self):
        from dataclasses import replace
        profile = replace(self.profile, skills=('Python', ' python ', 'PYTHON', 'AWS', '  '))
        result = score_job(sample_job('Python experience'), profile)
        baseline = score_job(sample_job('Python experience'), replace(self.profile, skills=('Python', 'AWS')))
        self.assertEqual(result.score, baseline.score)
        self.assertEqual(result.matched_skills, ('Python',))
        self.assertEqual(result.candidate_skills_not_mentioned_in_posting, ('AWS',))
        self.assertIn('Profile skills mentioned: 1/2 (25/50)', result.reasons)
        self.assertEqual(profile.skills, ('Python', ' python ', 'PYTHON', 'AWS', '  '))

    def test_duplicate_multiword_skills_use_same_whitespace_as_matching(self):
        from dataclasses import replace
        profile = replace(self.profile, skills=('Machine Learning', 'machine   learning', 'C', 'C++'))
        result = score_job(sample_job('Machine\tLearning and C++ experience'), profile)
        self.assertEqual(result.matched_skills, ('Machine Learning', 'C++'))
        self.assertEqual(result.candidate_skills_not_mentioned_in_posting, ('C',))
        self.assertIn('Profile skills mentioned: 2/3 (33/50)', result.reasons)
        reversed_profile = replace(profile, skills=('machine   learning', 'Machine Learning', 'C', 'C++'))
        reversed_result = score_job(sample_job('Machine Learning and C++ experience'), reversed_profile)
        self.assertEqual(reversed_result.matched_skills, ('machine   learning', 'C++'))
        self.assertEqual(reversed_result.score, result.score)

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

    def test_versioned_cpp_and_csharp_mentions_are_recognized(self):
        from dataclasses import replace
        preferences = replace(self.profile, skills=("C++", "C#"))
        self.assertEqual(score_job(sample_job("C++17 and C#12"), preferences).matched_skills, ("C++", "C#"))
        self.assertEqual(score_job(sample_job("C++extended and C#other"), preferences).matched_skills, ())

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
