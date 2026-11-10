"""Offline FastAPI workspace tests with a fictional candidate and no network calls."""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from career_agent.application import load_packet
from career_agent.models import CandidateProfile, Job
from career_agent.storage import Store

HAS_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


def fake_fact_bank():
    return {
        "name": "Fictional Candidate",
        "headline": "AI Program Delivery",
        "facts": [{
            "id": "experience-01",
            "section": "experience",
            "statement": "Delivered a fictional AI program involving Python and AWS.",
            "keywords": ["Python", "AWS"],
            "source_note": "Private fictional evidence that must never appear in the packet.",
            "verified_by_candidate": True,
        }],
    }


def fake_job(url="https://example.invalid/jobs/alpha"):
    return Job.from_mapping({
        "company": "Example Company",
        "title": "AI Program Lead",
        "location": "Remote USA",
        "job_url": url,
        "description": "Seeking Python and AWS. Visa sponsorship is available.",
        "is_remote": True,
    })


@unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
class DashboardTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "career.sqlite3"
        self.packet_dir = self.root / "packets"
        self.profile_path = self.root / "preferences.json"
        self.facts_path = self.root / "facts.json"
        profile = CandidateProfile(
            target_titles=("AI Program Lead",),
            skills=("Python", "AWS"),
            preferred_locations=(),
            allow_remote=True,
        )
        self.profile_path.write_text(json.dumps(asdict(profile)))
        self.facts_path.write_text(json.dumps(fake_fact_bank()))
        self.job = fake_job()
        with Store(self.db) as store:
            self.assertEqual(store.add_jobs([self.job]), 1)
        self.app = create_app(DashboardConfig(
            db_path=self.db,
            packet_dir=self.packet_dir,
            profile_path=self.profile_path,
            facts_path=self.facts_path,
        ))
        self.client = TestClient(self.app)
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        self.assertIn("data-session=", landing.text)
        match = re.search(r'data-session="([^"]+)"', landing.text)
        self.assertIsNotNone(match)
        self.session = match.group(1)

    def call(self, method, path, payload=None, token=None, headers=None):
        values = {"X-Session-Token": self.session if token is None else token}
        values.update(headers or {})
        if payload is None:
            return self.client.request(method, path, headers=values)
        return self.client.request(method, path, headers=values, json=payload)

    def test_dashboard_assets_and_security_headers(self):
        landing = self.client.get("/")
        self.assertIn("Content-Security-Policy", landing.headers)
        self.assertEqual(landing.headers["x-frame-options"], "DENY")
        for asset, snippet in [
            ("/assets/app.css", ".workspace"),
            ("/assets/app.js", "X-Session-Token"),
        ]:
            response = self.client.get(asset)
            self.assertEqual(response.status_code, 200)
            self.assertIn(snippet, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_no_token_cannot_read_jobs_or_write(self):
        denied = self.client.get("/api/jobs")
        self.assertEqual(denied.status_code, 403)
        invalid = self.call("GET", "/api/jobs", token="not-the-token")
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(
            self.client.post("/api/jobs", json={"company": "X", "title": "Y"}).status_code,
            403,
        )

    def test_host_origin_and_content_type_restrictions(self):
        self.assertEqual(
            self.call("GET", "/api/jobs", headers={"Host": "bad.example"}).status_code,
            400,
        )
        self.assertEqual(
            self.call("POST", "/api/jobs", {}, headers={"Origin": "https://evil.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/jobs", data="company=example",
                headers={"X-Session-Token": self.session, "Content-Type": "application/x-www-form-urlencoded"}
            ).status_code,
            415,
        )

    def test_overview_and_relevance(self):
        jobs = self.call("GET", "/api/jobs")
        self.assertEqual(jobs.status_code, 200)
        entries = jobs.json()["jobs"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["fit_score"], 100)
        self.assertEqual(entries[0]["sponsorship_status"], "posting_claims_sponsorship")
        self.assertEqual(entries[0]["review_decision"], None)
        overview = self.call("GET", "/api/overview").json()
        self.assertEqual(overview["total_jobs"], 1)
        self.assertEqual(overview["submitted_recorded"], 0)
        self.assertFalse(overview["submission_sent_by_application"])

    def test_add_job_dedup_and_validation(self):
        value = {
            "company": "Second Company",
            "title": "AI Manager",
            "location": "Boston, MA",
            "job_url": "https://example.invalid/jobs/second",
            "description": "AI governance",
            "is_remote": False,
        }
        created = self.call("POST", "/api/jobs", value)
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["created"])
        repeated = self.call("POST", "/api/jobs", value)
        self.assertFalse(repeated.json()["created"])
        invalid = self.call("POST", "/api/jobs", {**value, "job_url": "javascript:alert(1)"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.call("GET", "/api/jobs").json()["total"], 2)

    def test_requires_configured_verified_facts_to_draft(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app

        other = TestClient(create_app(DashboardConfig(
            db_path=self.db, packet_dir=self.packet_dir,
        )))
        token = re.search(r'data-session="([^"]+)"', other.get("/").text).group(1)
        denied = other.post(
            f"/api/jobs/{self.job.id}/draft",
            json={}, headers={"X-Session-Token": token},
        )
        self.assertEqual(denied.status_code, 412)
        self.assertFalse((self.packet_dir / f"{self.job.id}.json").exists())

    def _draft(self):
        response = self.call("POST", f"/api/jobs/{self.job.id}/draft", {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["packet_sha256"]

    def test_draft_review_and_manual_submission_workflow(self):
        job_id = self.job.id
        draft_id = self._draft()
        packet_resp = self.call("GET", f"/api/jobs/{job_id}/packet")
        self.assertEqual(packet_resp.status_code, 200)
        packet = packet_resp.json()["packet"]
        self.assertEqual(packet["packet_sha256"], draft_id)
        self.assertIn("Delivered a fictional AI program", packet["resume_markdown"])
        self.assertNotIn("Private fictional evidence", json.dumps(packet_resp.json()))
        self.assertFalse(packet["submission_sent"])
        self.assertEqual(load_packet(self.packet_dir / f"{job_id}.json")["packet_sha256"], draft_id)

        unapproved = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id,
            "receipt": "employer-receipt-123",
            "attest_confirmed": True,
        })
        self.assertEqual(unapproved.status_code, 412)
        missing_attestation = self.call("POST", f"/api/jobs/{job_id}/decision", {
            "packet_sha256": draft_id,
            "approved": True,
        })
        self.assertEqual(missing_attestation.status_code, 422)
        approved = self.call("POST", f"/api/jobs/{job_id}/decision", {
            "packet_sha256": draft_id,
            "approved": True,
            "acknowledged": True,
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["decision"], "approved_for_manual_submission")
        self.assertFalse(approved.json()["submission_sent"])
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(job_id), "discovered")
            self.assertEqual(store.latest_review(job_id, draft_id)["decision"], "approved_for_manual_submission")

        reviewed = self.call("GET", f"/api/jobs/{job_id}/packet").json()
        self.assertEqual(reviewed["review"]["decision"], "approved_for_manual_submission")
        events = self.call("GET", "/api/reviews").json()["reviews"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("Private fictional evidence", json.dumps(events))

        no_receipt = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id, "receipt": "", "attest_confirmed": True,
        })
        self.assertEqual(no_receipt.status_code, 422)
        submitted = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id,
            "receipt": "employer-receipt-123",
            "attest_confirmed": True,
        })
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertFalse(submitted.json()["submission_sent_by_application"])
        self.assertEqual(submitted.json()["application_stage"], "submitted")
        self.assertEqual(self.call("GET", "/api/overview").json()["submitted_recorded"], 1)
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(job_id), "submitted")
        self.assertEqual(self.call("POST", f"/api/jobs/{job_id}/decision", {
            "approved": False, "acknowledged": True, "packet_sha256": draft_id,
        }).status_code, 422)

    def test_old_approval_not_valid_for_regenerated_packet(self):
        old_hash = self._draft()
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/decision", {
            "approved": True, "acknowledged": True, "packet_sha256": old_hash,
        }).status_code, 200)
        modified = fake_fact_bank()
        modified["facts"][0]["statement"] = "Delivered a fictional revised AWS program with independent review."
        self.facts_path.write_text(json.dumps(modified))
        new_hash = self._draft()
        self.assertNotEqual(old_hash, new_hash)
        self.assertIsNone(self.call("GET", f"/api/jobs/{self.job.id}/packet").json()["review"])
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/decision", {
            "approved": True, "acknowledged": True, "packet_sha256": old_hash,
        }).status_code, 409)
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/submitted", {
            "packet_sha256": new_hash, "receipt": "actual-receipt",
            "attest_confirmed": True,
        }).status_code, 412)

    def test_bad_job_ids_and_bad_payloads(self):
        self.assertEqual(self.call("GET", "/api/jobs/../../private/packet").status_code, 404)
        self.assertEqual(self.call("GET", "/api/jobs/not-an-id/packet").status_code, 404)
        self.assertEqual(self.call("POST", "/api/jobs", ["array-not-object"]).status_code, 422)
        self.assertEqual(self.call("POST", "/api/jobs", {"company": "No URL", "title": "Missing link"}).status_code, 422)

    def test_bounded_json_body_and_forged_session_are_rejected(self):
        large = {"company": "Example", "title": "Too big", "job_url": "https://example.invalid/large", "description": "X" * (129 * 1024)}
        response = self.call("POST", "/api/jobs", large)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.call("GET", "/api/jobs", token="another-process-token").status_code, 403)

    def test_local_job_untrusted_html_is_only_json_data(self):
        hostile = {
            "company": "<img src=x onerror=alert(1)>",
            "title": "<script>alert('injected')</script>",
            "job_url": "https://example.invalid/jobs/hostile",
            "description": "A <b>markup</b> job",
        }
        created = self.call("POST", "/api/jobs", hostile)
        self.assertEqual(created.status_code, 201)
        response = self.call("GET", "/api/jobs")
        self.assertEqual(response.status_code, 200)
        job = next(j for j in response.json()["jobs"] if j["id"] == created.json()["job_id"])
        self.assertEqual(job["title"], hostile["title"])
        self.assertNotIn(hostile["title"], self.client.get("/").text)
        # Frontend uses textContent; untrusted strings are never interpreted as HTML.
        source = self.client.get("/assets/app.js").text
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)

    def test_no_candidate_claims_embedded_in_homepage(self):
        self.assertNotIn("Fictional Candidate", self.client.get("/").text)
        self.assertNotIn("Delivered a fictional AI program", self.client.get("/").text)


class ServerBindingTests(unittest.TestCase):
    @unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
    def test_loopback_only_binding_and_port_limits(self):
        from unittest.mock import patch
        from career_agent.dashboard import DashboardConfig, serve
        with patch("uvicorn.run") as run:
            serve(DashboardConfig(), port=8765)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 8765)
            self.assertFalse(run.call_args.kwargs["access_log"])
        with self.assertRaises(ValueError):
            serve(DashboardConfig(), port=443)


class ReviewStoreTests(unittest.TestCase):
    def test_review_history_is_append_only_and_packet_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "db.sqlite3") as store:
                job = fake_job()
                store.add_jobs([job])
                a = "a" * 64
                b = "b" * 64
                store.record_review(job.id, a, approved=True)
                store.record_review(job.id, a, approved=False)
                store.record_review(job.id, b, approved=True)
                self.assertEqual(len(store.review_history()), 3)
                self.assertEqual(store.latest_review(job.id, a)["decision"], "rejected_by_reviewer")
                self.assertEqual(store.latest_review(job.id, b)["decision"], "approved_for_manual_submission")
                self.assertIsNone(store.latest_review(job.id, "c" * 64))

    def test_non_boolean_review_and_invalid_sha_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "db.sqlite3") as store:
                job = fake_job()
                store.add_jobs([job])
                with self.assertRaises(ValueError):
                    store.record_review(job.id, "a" * 64, approved="true")
                with self.assertRaises(ValueError):
                    store.record_review(job.id, "invalid", approved=True)


if __name__ == "__main__":
    unittest.main()
