"""F07 CRM regression tests: local stage integrity, contacts, follow-ups and API."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from career_agent.crm import ApplicationCRM
from career_agent.models import Job
from career_agent.storage import Store

try:
    from fastapi.testclient import TestClient
    from career_agent.dashboard import DashboardConfig, create_app
except ImportError:
    TestClient = None


def sample_job(suffix="alpha") -> Job:
    return Job.from_mapping({
        "title": "AI Transformation Manager",
        "company": "Example Employer",
        "location": "Remote, USA",
        "job_url": "https://example.invalid/jobs/" + suffix,
        "description": "Fictional test posting.",
    })


class CRMStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "local.sqlite3"
        self.store = Store(self.db)
        self.addCleanup(self.store.connection.close)
        self.job = sample_job()
        self.second = sample_job("beta")
        self.store.add_jobs([self.job, self.second])
        self.crm = ApplicationCRM(self.store)

    def test_preapplication_stages_do_not_submit_applications(self):
        self.assertEqual(self.crm.details(self.job.id)["stage"], "discovered")
        self.assertEqual(self.crm.change_stage(self.job.id, "saved")["stage"], "saved")
        self.crm.change_stage(self.job.id, "shortlisted", "Good product match.")
        self.assertEqual(self.crm.details(self.job.id)["stage"], "shortlisted")
        self.assertEqual(self.store.stage_of(self.job.id), "discovered")
        self.assertEqual(self.crm.stage_history(self.job.id)[0]["note"], "Good product match.")
        self.assertEqual(self.crm.stage_history(self.job.id)[0]["actor"], "candidate")
        with self.assertRaisesRegex(ValueError, "Cannot move"):
            self.crm.change_stage(self.job.id, "screening")
        with self.assertRaisesRegex(ValueError, "Cannot move"):
            self.crm.change_stage(self.job.id, "applied")
        self.assertEqual(self.store.stage_of(self.job.id), "discovered")

    def test_postapplication_interview_and_offer_events(self):
        self.crm.change_stage(self.job.id, "saved")
        self.store.stage(self.job.id)
        self.store.confirm(self.job.id, "employer-123", attested=True)
        self.assertEqual(self.crm.details(self.job.id)["stage"], "applied")
        for new_stage in ("screening", "interview", "offer", "accepted"):
            self.crm.change_stage(self.job.id, new_stage)
        details = self.crm.details(self.job.id)
        self.assertEqual(details["stage"], "accepted")
        self.assertEqual(len(details["stage_events"]), 5)
        self.assertEqual(details["allowed_transitions"], [])
        with self.assertRaisesRegex(ValueError, "Cannot move"):
            self.crm.change_stage(self.job.id, "screening")
        self.assertEqual(self.store.stage_of(self.job.id), "submitted")

    def test_stage_validation_and_independent_jobs(self):
        with self.assertRaises(ValueError):
            self.crm.change_stage(self.job.id, "made_up_stage")
        with self.assertRaises(ValueError):
            self.crm.change_stage(self.job.id, "saved", "A" * 1001)
        with self.assertRaises(ValueError):
            self.crm.change_stage("not-a-job", "saved")
        self.crm.change_stage(self.job.id, "archived")
        self.assertEqual(self.crm.details(self.second.id)["stage"], "discovered")
        self.assertEqual(self.crm.summary()["stages"]["archived"], 1)

    def test_recruiter_contacts_and_local_privacy(self):
        item = self.crm.add_contact(
            self.job.id, "Fictional Recruiter", role="Talent Partner",
            email="recruiter@example.invalid", notes="Called about interview loop.",
        )
        rows = self.crm.contacts(self.job.id)
        self.assertEqual(rows[0]["contact_id"], item["contact_id"])
        self.assertEqual(rows[0]["email"], "recruiter@example.invalid")
        self.assertEqual(self.crm.contacts(self.second.id), [])
        for invalid in ("person-at-email", "x@y", "a b@example.com"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "email"):
                    self.crm.add_contact(self.job.id, "Person", email=invalid)
        with self.assertRaises(ValueError):
            self.crm.add_contact(self.job.id, "")
        self.assertEqual(self.crm.summary()["open_followups"], [])

    def test_followup_linked_to_same_job_and_overdue(self):
        person = self.crm.add_contact(self.job.id, "Hiring manager")
        result = self.crm.add_followup(
            self.job.id, "Send thank-you draft for review", "2020-01-02",
            person["contact_id"],
        )
        upcoming = self.crm.summary()["open_followups"]
        self.assertEqual(upcoming[0]["title"], result["title"])
        self.assertTrue(upcoming[0]["overdue"])
        self.assertEqual(self.crm.followups(self.job.id)[0]["contact_name"], "Hiring manager")
        self.assertEqual(self.crm.followups(self.second.id), [])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.crm.add_followup(self.second.id, "Ask for feedback", "2026-11-21", person["contact_id"])
        for invalid in ("2026-02-30", "11/20/2026", None):
            with self.assertRaises(ValueError):
                self.crm.add_followup(self.job.id, "Invalid due date", invalid)

    def test_reschedule_complete_and_no_automatic_sending(self):
        task = self.crm.add_followup(self.job.id, "Check recruiter response", "2026-11-20")
        task_id = task["followup_id"]
        result = self.crm.update_followup(task_id, due_date="2026-11-22")
        self.assertEqual(result["action"], "rescheduled")
        self.assertEqual(self.crm.followups(self.job.id)[0]["due_date"], "2026-11-22")
        self.assertEqual(self.crm.update_followup(task_id, complete=True)["action"], "completed")
        self.assertFalse(self.crm.summary()["open_followups"])
        with self.assertRaisesRegex(ValueError, "Completed"):
            self.crm.update_followup(task_id, complete=True)
        with self.assertRaises(ValueError):
            self.crm.update_followup(task_id, due_date="2026-11-24")
        self.assertEqual(
            [r["action"] for r in self.store.connection.execute(
                "SELECT action FROM crm_followup_events WHERE followup_id=? ORDER BY event_id",
                (task_id,),
            )],
            ["created", "rescheduled", "completed"],
        )

    def test_restart_preserves_legacy_job_data(self):
        self.crm.change_stage(self.job.id, "saved")
        self.crm.add_contact(self.job.id, "Recruiter")
        self.store.connection.close()
        # Prevent tearDown from trying to close an already-closed connection.
        self.store = Store(self.db)
        refreshed = ApplicationCRM(self.store)
        self.assertEqual(refreshed.details(self.job.id)["stage"], "saved")
        self.assertEqual(refreshed.contacts(self.job.id)[0]["name"], "Recruiter")
        self.assertEqual(self.store.stage_of(self.job.id), "discovered")


@unittest.skipUnless(TestClient is not None, 'Install web test extras: pip install -e ".[web,dev]"')
class CRMApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "jobs.sqlite3"
        self.job = sample_job()
        self.second = sample_job("second")
        with Store(self.db) as store:
            store.add_jobs([self.job, self.second])
        self.client = TestClient(create_app(DashboardConfig(
            db_path=self.db,
            packet_dir=self.root / "packets",
        )), base_url="http://localhost")
        source = self.client.get("/").text
        self.token = re.search(r'data-session="([^"]+)"', source).group(1)

    def api(self, method, path, body=None, token=None):
        headers = {"X-Session-Token": self.token if token is None else token}
        if body is None:
            return self.client.request(method, path, headers=headers)
        return self.client.request(method, path, headers=headers, json=body)

    def test_api_auth_and_basic_tracking(self):
        self.assertEqual(self.client.get("/api/crm").status_code, 403)
        self.assertEqual(self.api("GET", "/api/crm", token="bad").status_code, 403)
        start = self.api("GET", f"/api/jobs/{self.job.id}/crm")
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.json()["stage"], "discovered")
        changed = self.api("POST", f"/api/jobs/{self.job.id}/crm/stage", {
            "stage": "saved", "note": "Priority role",
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.api("GET", f"/api/jobs/{self.job.id}/crm").json()["stage"], "saved")
        self.assertEqual(self.api("GET", "/api/crm").json()["stages"]["saved"], 1)
        self.assertEqual(self.api("GET", "/api/overview").json()["submitted_recorded"], 0)

    def test_cannot_fake_applied_status_with_pipeline_update(self):
        res = self.api("POST", f"/api/jobs/{self.job.id}/crm/stage", {"stage": "applied"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.api("GET", f"/api/jobs/{self.job.id}/crm").json()["stage"], "discovered")
        with Store(self.db) as store:
            store.stage(self.job.id)
            store.confirm(self.job.id, "real-employer-ref", attested=True)
        self.assertEqual(self.api("GET", f"/api/jobs/{self.job.id}/crm").json()["stage"], "applied")
        changed = self.api("POST", f"/api/jobs/{self.job.id}/crm/stage", {"stage": "screening"})
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(self.api("GET", f"/api/jobs/{self.job.id}/crm").json()["application_submitted"])

    def test_contacts_followup_completion_and_rescheduling(self):
        add = self.api("POST", f"/api/jobs/{self.job.id}/crm/contacts", {
            "name": "Example Recruiter", "role": "Talent Partner",
            "email": "recruiter@example.invalid", "notes": "Sent a message externally.",
        })
        self.assertEqual(add.status_code, 201, add.text)
        contact_id = add.json()["contact_id"]
        task = self.api("POST", f"/api/jobs/{self.job.id}/crm/followups", {
            "title": "Follow up after interview", "due_date": "2026-11-20",
            "contact_id": contact_id,
        })
        self.assertEqual(task.status_code, 201, task.text)
        task_id = task.json()["followup_id"]
        self.assertEqual(self.api("GET", "/api/crm").json()["open_followups"][0]["title"], "Follow up after interview")
        moved = self.api("POST", f"/api/crm/followups/{task_id}/reschedule", {"due_date": "2026-11-23"})
        self.assertEqual(moved.status_code, 200)
        no_attest = self.api("POST", f"/api/crm/followups/{task_id}/complete", {})
        self.assertEqual(no_attest.status_code, 422)
        completed = self.api("POST", f"/api/crm/followups/{task_id}/complete", {"acknowledged": True})
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(self.api("GET", "/api/crm").json()["open_followups"], [])
        details = self.api("GET", f"/api/jobs/{self.job.id}/crm").json()
        self.assertEqual(details["followups"][0]["due_date"], "2026-11-23")
        self.assertIsNotNone(details["followups"][0]["completed_at"])
        self.assertEqual(len(details["contacts"]), 1)
        self.assertEqual(self.api("GET", "/api/overview").json()["submitted_recorded"], 0)

    def test_input_validation_and_cross_job_contact(self):
        self.assertEqual(self.api("GET", "/api/jobs/not-a-real-job/crm").status_code, 404)
        self.assertEqual(self.api("POST", f"/api/jobs/{self.job.id}/crm/contacts", {
            "name": "<script>alert(1)</script>", "email": "bad-email",
        }).status_code, 422)
        person = self.api("POST", f"/api/jobs/{self.job.id}/crm/contacts", {
            "name": "Fictional Recruiter",
        }).json()["contact_id"]
        self.assertEqual(self.api("POST", f"/api/jobs/{self.second.id}/crm/followups", {
            "title": "Should be rejected", "due_date": "2026-11-24",
            "contact_id": person,
        }).status_code, 422)
        invalid = self.api("POST", f"/api/jobs/{self.job.id}/crm/followups", {
            "title": "Invalid date", "due_date": "2026-02-29",
        })
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
