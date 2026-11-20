import csv
import io
import tempfile
import unittest
from pathlib import Path

from career_agent.crm import ApplicationCRM
from career_agent.models import Job
from career_agent.storage import Store
from career_agent.tracker_import import COLUMNS, import_tracker


def tracker_csv(*rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return output.getvalue()


class TrackerImportTests(unittest.TestCase):
    def test_links_prior_match_retains_other_applications_and_preserves_manual_work(self):
        first = ["2025-01-10", "Example", "Analyst", "Portal", "Under review", "Receipt A"]
        second = ["≤ 2025-01-10", "Example", "", "Email", "Applied", "Message B"]
        third = [*second[:-1], "Message C"]
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "test.db") as store:
            crm = ApplicationCRM(store)
            saved = Job("Example", "Analyst", "", "https://example.org/jobs/1")
            store.add_jobs([saved])
            with store.connection:
                store.archive_job(saved.id, "tracker", evidence={"tracker": dict(zip(COLUMNS, first))})
            initial = tracker_csv(first, second, third)
            counts = import_tracker(crm, initial)
            self.assertEqual((counts["linked"], counts["created"]), (1, 2))
            self.assertEqual(import_tracker(crm, initial)["unchanged"], 3)
            rows = crm.summary()["job_stages"]
            self.assertEqual(len(rows), 3)
            self.assertEqual(crm.details(saved.id)["stage"], "under_review")
            self.assertEqual(crm.outcomes()["submitted"], 3)
            self.assertEqual(len(store.job_page(include_archived=True)[0]), 1)
            self.assertEqual(len(store.list_jobs(include_archived=True)), 1)
            free = next(r for r in rows if r["job_id"] != saved.id)
            self.assertIsNone(crm.details(free["job_id"])["delivery"])
            crm.change_stage(free["job_id"], "interview")
            changed = tracker_csv(first, [*second[:4], "Closed", second[-1]], [*third[:4], "Closed", third[-1]])
            self.assertEqual(import_tracker(crm, changed)["updated"], 2)
            self.assertEqual(crm.details(free["job_id"])["stage"], "interview")
            other = next(r["job_id"] for r in rows if r["job_id"] not in (saved.id, free["job_id"]))
            self.assertEqual(crm.details(other)["stage"], "closed")
            self.assertEqual(crm.details(other)["external_application"]["applied_date"], second[0])
            self.assertEqual(len(crm.export_history()["records"]["external_applications"]), 3)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM applications").fetchone()[0], 0)

    def test_invalid_row_rolls_back_entire_import(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "test.db") as store:
            crm = ApplicationCRM(store)
            with self.assertRaisesRegex(ValueError, "line 3"):
                import_tracker(crm, tracker_csv(["2025-01-01","Example","Role","","Applied",""],
                                               ["2025-01-02","","Role","","Applied",""]))
            self.assertEqual(crm.summary()["job_stages"], [])
