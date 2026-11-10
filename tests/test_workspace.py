"""R1 candidate workspace: versioning, verification and local dashboard integration."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from career_agent.candidate import FactBank
from career_agent.models import CandidateProfile, Job
from career_agent.storage import Store
from career_agent.workspace import CandidateWorkspace, StaleRevision, blank_workspace

try:
    from fastapi.testclient import TestClient
    from career_agent.dashboard import DashboardConfig, create_app
except ImportError:
    TestClient = None


def preferences(name="Fictional Candidate"):
    return {
        "name": name, "headline": "AI Program Delivery",
        "target_titles": ["AI Lead"], "skills": ["AWS", "Python"],
        "preferred_locations": ["Knoxville"], "allow_remote": True,
    }


def fact(text="Delivered an imaginary AWS modernization proof of concept.", verified=False):
    return {
        "id": "pilot-01", "section": "projects", "statement": text,
        "keywords": ["AWS"], "source_note": "Private fictional project note",
        "verified_by_candidate": verified,
    }


def new_job():
    return Job.from_mapping({
        "title": "AI Lead", "company": "Example Test Employer",
        "job_url": "https://example.invalid/jobs/ai-lead",
        "location": "Knoxville, TN",
        "description": "Leading AWS migration projects",
    })


class WorkspaceModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "career.sqlite3")
        self.addCleanup(self.store.connection.close)
        self.ws = CandidateWorkspace(self.store)

    def test_blank_document_requires_explicit_first_save(self):
        self.assertEqual(self.ws.latest(), blank_workspace())
        self.assertEqual(self.ws.history(), [])
        updated = self.ws.save(0, preferences(), [fact(verified=True)])
        self.assertEqual(updated["revision"], 1)
        self.assertFalse(updated["facts"][0]["verified_by_candidate"])
        self.assertEqual(self.ws.history()[0]["change_type"], "edited")

    def test_fact_must_be_verified_in_separate_action(self):
        saved = self.ws.save(0, preferences(), [fact()])
        with self.assertRaisesRegex(ValueError, "Confirm at least one"):
            CandidateWorkspace.fact_bank(saved)
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.ws.verify(1, "pilot-01", True, acknowledged=False)
        approved = self.ws.verify(1, "pilot-01", True, acknowledged=True)
        self.assertTrue(approved["facts"][0]["verified_by_candidate"])
        bank = CandidateWorkspace.fact_bank(approved)
        self.assertEqual(bank.facts[0].fact_id, "pilot-01")
        self.assertEqual(bank.facts[0].statement, fact()["statement"])
        self.assertEqual(len(self.ws.history()), 2)

    def test_changed_fact_is_automatically_unverified(self):
        first = self.ws.save(0, preferences(), [fact()])
        confirmed = self.ws.verify(first["revision"], "pilot-01", True, True)
        edited = self.ws.save(
            confirmed["revision"], preferences(),
            [fact(text="Built a fictional AWS automation system.", verified=True)],
        )
        self.assertFalse(edited["facts"][0]["verified_by_candidate"])
        with self.assertRaisesRegex(ValueError, "Confirm at least one"):
            CandidateWorkspace.fact_bank(edited)
        reverified = self.ws.verify(edited["revision"], "pilot-01", True, True)
        self.assertTrue(reverified["facts"][0]["verified_by_candidate"])

    def test_revocation_and_delete_are_audited(self):
        saved = self.ws.save(0, preferences(), [fact()])
        checked = self.ws.verify(saved["revision"], "pilot-01", True, True)
        revoked = self.ws.verify(checked["revision"], "pilot-01", False, True)
        self.assertFalse(revoked["facts"][0]["verified_by_candidate"])
        removed = self.ws.save(revoked["revision"], preferences(), [])
        self.assertEqual(removed["facts"], [])
        self.assertEqual([x["change_type"] for x in self.ws.history()], [
            "edited", "revoked", "verified", "edited",
        ])

    def test_stale_revision_does_not_lose_changes(self):
        updated = self.ws.save(0, preferences(), [])
        with self.assertRaises(StaleRevision):
            self.ws.save(0, preferences("Overwriting Candidate"), [])
        self.assertEqual(self.ws.latest(), updated)
        with self.assertRaises(StaleRevision):
            self.ws.verify(0, "pilot-01", True, True)

    def test_no_invented_verification_through_direct_save(self):
        seeded = self.ws.save(0, preferences(), [fact(verified=True)])
        self.assertFalse(seeded["facts"][0]["verified_by_candidate"])
        with self.assertRaises(ValueError):
            self.ws.save(1, preferences(), [dict(fact(), verified_by_candidate="yes")])
        self.assertEqual(self.ws.latest()["revision"], 1)

    def test_previously_verified_unmodified_fact_is_preserved(self):
        seeded = self.ws.save(0, preferences(), [fact()])
        checked = self.ws.verify(seeded["revision"], "pilot-01", True, True)
        modified_profile = preferences()
        modified_profile["target_titles"] = ["AI Director"]
        changed = self.ws.save(checked["revision"], modified_profile, [fact(verified=True)])
        self.assertTrue(changed["facts"][0]["verified_by_candidate"])
        self.assertEqual(CandidateWorkspace.scoring_profile(changed).target_titles, ("AI Director",))

    def test_validation_rejects_invalid_or_duplicate_facts(self):
        bad = dict(fact())
        bad["id"] = "pilot-01"
        for document in ([fact(), bad], [dict(fact(), section="unknown")],
                         [dict(fact(), source_note="")]):
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    self.ws.save(0, preferences(), document)
        self.assertEqual(self.ws.latest()["revision"], 0)

    def test_legacy_import_preserves_source_files_and_verification(self):
        profile = CandidateProfile(
            target_titles=("AI Lead",), skills=("AWS",),
            preferred_locations=("Knoxville",), allow_remote=True,
        )
        bank = FactBank.parse({
            "name": "Fictional Candidate",
            "headline": "AI Delivery",
            "facts": [fact(verified=True)],
        })
        imported = self.ws.import_legacy(profile, bank)
        self.assertEqual(imported["revision"], 1)
        self.assertTrue(imported["facts"][0]["verified_by_candidate"])
        with self.assertRaises(StaleRevision):
            self.ws.import_legacy(profile, bank)

    def test_verification_requires_candidate_name(self):
        saved = self.ws.save(0, preferences(""), [fact()])
        with self.assertRaisesRegex(ValueError, "name"):
            self.ws.verify(saved["revision"], "pilot-01", True, True)
        self.assertEqual(self.ws.latest()["revision"], 1)


@unittest.skipUnless(TestClient is not None, "FastAPI / HTTPX optional web extras required")
class WorkspaceDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.db = folder / "jobs.sqlite3"
        self.profile_file = folder / "legacy-profile.json"
        self.facts_file = folder / "legacy-facts.json"
        self.profile_file.write_text(json.dumps({
            "target_titles": ["AI Lead"], "skills": ["AWS"],
            "preferred_locations": ["Knoxville"], "allow_remote": True,
        }))
        self.facts_file.write_text(json.dumps({
            "name": "Legacy Demo Candidate", "headline": "AI Practitioner",
            "facts": [fact(verified=True)],
        }))
        self.profile_bytes = self.profile_file.read_bytes()
        self.facts_bytes = self.facts_file.read_bytes()
        self.job = new_job()
        with Store(self.db) as store:
            store.add_jobs([self.job])
        self.client = TestClient(create_app(DashboardConfig(
            db_path=self.db,
            packet_dir=folder / "packets",
            profile_path=self.profile_file,
            facts_path=self.facts_file,
        )), base_url="http://localhost")
        landing = self.client.get("/")
        self.token = re.search(r'data-session="([^"]+)"', landing.text).group(1)

    def api(self, method, url, data=None, token=None):
        headers = {"X-Session-Token": self.token if token is None else token}
        if data is None:
            return self.client.request(method, url, headers=headers)
        return self.client.request(method, url, headers=headers, json=data)

    def test_endpoints_guarded_by_existing_session(self):
        self.assertEqual(self.client.get("/api/workspace").status_code, 403)
        self.assertEqual(self.api("GET", "/api/workspace", token="bad").status_code, 403)
        self.assertEqual(self.api("GET", "/api/workspace").status_code, 200)

    def test_legacy_import_only_when_explicit_and_does_not_edit_source(self):
        data = self.api("GET", "/api/workspace").json()["workspace"]
        self.assertEqual(data["revision"], 0)
        self.assertTrue(self.api("GET", "/api/settings").json()["legacy_import_available"])
        self.assertEqual(self.api("POST", "/api/workspace/import", {}).status_code, 422)
        result = self.api("POST", "/api/workspace/import", {"acknowledged": True})
        self.assertEqual(result.status_code, 200, result.text)
        imported = result.json()["workspace"]
        self.assertEqual(imported["revision"], 1)
        self.assertTrue(imported["facts"][0]["verified_by_candidate"])
        self.assertEqual(self.profile_file.read_bytes(), self.profile_bytes)
        self.assertEqual(self.facts_file.read_bytes(), self.facts_bytes)
        self.assertEqual(self.api("POST", "/api/workspace/import", {"acknowledged": True}).status_code, 409)
        self.assertEqual(self.api("GET", "/api/workspace/history").json()["revisions"][0]["change_type"], "imported_legacy")

    def test_unverified_fact_never_in_draft_and_confirmed_fact_does(self):
        fresh = self.api("PUT", "/api/workspace", {
            "expected_revision": 0, "profile": preferences(), "facts": [fact()],
        })
        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertFalse(fresh.json()["workspace"]["facts"][0]["verified_by_candidate"])
        self.assertFalse(self.api("GET", "/api/settings").json()["facts_configured"])
        denied = self.api("POST", f"/api/jobs/{self.job.id}/draft", {})
        self.assertEqual(denied.status_code, 422)
        verified = self.api("POST", "/api/workspace/facts/pilot-01/verification", {
            "expected_revision": 1, "verified": True, "acknowledged": True,
        })
        self.assertEqual(verified.status_code, 200, verified.text)
        draft = self.api("POST", f"/api/jobs/{self.job.id}/draft", {})
        self.assertEqual(draft.status_code, 200, draft.text)
        packet = self.api("GET", f"/api/jobs/{self.job.id}/packet").json()["packet"]
        self.assertIn(fact()["statement"], packet["resume_markdown"])
        self.assertNotIn("Private fictional project note", packet["resume_markdown"])
        self.assertEqual(packet["candidate_name"], "Fictional Candidate")
        self.assertTrue(self.api("GET", "/api/settings").json()["facts_configured"])

    def test_fact_revocation_invalidates_prior_approval_and_prevents_submission(self):
        saved = self.api("PUT", "/api/workspace", {
            "expected_revision": 0, "profile": preferences(), "facts": [fact()],
        })
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.api("POST", "/api/workspace/facts/pilot-01/verification", {
            "expected_revision": 1, "verified": True, "acknowledged": True,
        }).status_code, 200)
        draft = self.api("POST", f"/api/jobs/{self.job.id}/draft", {})
        self.assertEqual(draft.status_code, 200)
        fingerprint = draft.json()["packet_sha256"]
        packet = self.api("GET", f"/api/jobs/{self.job.id}/packet").json()["packet"]
        self.assertEqual(packet["candidate_workspace_revision"], 2)
        approved = self.api("POST", f"/api/jobs/{self.job.id}/decision", {
            "packet_sha256": fingerprint, "approved": True, "acknowledged": True,
        })
        self.assertEqual(approved.status_code, 200)

        revoked = self.api("POST", "/api/workspace/facts/pilot-01/verification", {
            "expected_revision": 2, "verified": False, "acknowledged": True,
        })
        self.assertEqual(revoked.status_code, 200)
        latest = self.api("GET", f"/api/jobs/{self.job.id}/packet").json()
        self.assertTrue(latest["stale"])
        self.assertIsNone(latest["review"])
        jobs = self.api("GET", "/api/jobs").json()["jobs"]
        self.assertTrue(jobs[0]["packet_stale"])
        self.assertIsNone(jobs[0]["review_decision"])
        rejected = self.api("POST", f"/api/jobs/{self.job.id}/submitted", {
            "packet_sha256": fingerprint, "receipt": "external-receipt-123",
            "attest_confirmed": True,
        })
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.api("POST", f"/api/jobs/{self.job.id}/decision", {
            "packet_sha256": fingerprint, "approved": True, "acknowledged": True,
        }).status_code, 409)
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(self.job.id), "discovered")

    def test_changed_verified_fact_is_excluded_from_new_draft(self):
        self.assertEqual(self.api("PUT", "/api/workspace", {
            "expected_revision": 0, "profile": preferences(), "facts": [fact()],
        }).status_code, 200)
        self.assertEqual(self.api("POST", "/api/workspace/facts/pilot-01/verification", {
            "expected_revision": 1, "verified": True, "acknowledged": True,
        }).status_code, 200)
        changed = self.api("PUT", "/api/workspace", {
            "expected_revision": 2,
            "profile": preferences(),
            "facts": [fact("Delivered a fictional revised project.", verified=True)],
        })
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()["workspace"]["facts"][0]["verified_by_candidate"])
        self.assertEqual(self.api("POST", f"/api/jobs/{self.job.id}/draft", {}).status_code, 422)

    def test_stale_profile_update_returns_409_and_keeps_data(self):
        one = self.api("PUT", "/api/workspace", {
            "expected_revision": 0, "profile": preferences(), "facts": [],
        })
        self.assertEqual(one.status_code, 200)
        lost = self.api("PUT", "/api/workspace", {
            "expected_revision": 0, "profile": preferences("Stale Editor"), "facts": [],
        })
        self.assertEqual(lost.status_code, 409)
        self.assertEqual(self.api("GET", "/api/workspace").json()["workspace"]["profile"]["name"], "Fictional Candidate")

    def test_largest_supported_fact_bank_is_editable_through_full_save(self):
        many = [
            {
                "id": f"fact-{i:03d}", "section": "projects",
                "statement": "A" * 1000, "keywords": ["AWS"],
                "source_note": "B" * 500,
                "verified_by_candidate": False,
            }
            for i in range(100)
        ]
        payload = {"expected_revision": 0, "profile": preferences(), "facts": many}
        self.assertGreater(len(json.dumps(payload)), 128 * 1024)
        created = self.api("PUT", "/api/workspace", payload)
        self.assertEqual(created.status_code, 200, created.text[:200])
        self.assertEqual(len(created.json()["workspace"]["facts"]), 100)
        changed = dict(preferences(), headline="Revised fictional headline")
        updated = self.api("PUT", "/api/workspace", {
            "expected_revision": 1, "profile": changed, "facts": many,
        })
        self.assertEqual(updated.status_code, 200, updated.text[:200])
        self.assertEqual(updated.json()["workspace"]["revision"], 2)
        self.assertEqual(updated.json()["workspace"]["profile"]["headline"], changed["headline"])

    def test_workspace_limit_is_bounded(self):
        oversized = {
            "expected_revision": 0,
            "profile": dict(preferences(), headline="X" * (2 * 1024 * 1024)),
            "facts": [],
        }
        result = self.api("PUT", "/api/workspace", oversized)
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.api("GET", "/api/workspace").json()["workspace"]["revision"], 0)

    def test_ui_contains_profile_editor_and_safe_text_rendering(self):
        html = self.client.get("/").text
        self.assertIn('id="profile-screen"', html)
        self.assertIn('id="career-profile-form"', html)
        self.assertIn('id="career-fact-form"', html)
        source = self.client.get("/assets/app.js").text
        self.assertIn('"/api/workspace"', source)
        self.assertIn("textContent", source)
        self.assertNotRegex(source, r"\b(?:innerHTML|outerHTML)\s*=")


if __name__ == "__main__":
    unittest.main()
