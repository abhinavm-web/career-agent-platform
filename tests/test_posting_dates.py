"""Source-reported dates are observations, never availability claims."""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from career_agent.candidate import FactBank
from career_agent.discovery import discover_jobspy
from career_agent.models import CandidateProfile, Job, posting_date
from career_agent.storage import Store
from career_agent.workflows import inputs, signature
from test_dashboard import fake_fact_bank, fake_job


def record(posted, source='indeed'):
    return {'company': 'Fictional Company', 'title': 'Engineer', 'job_url': 'https://example.invalid/role',
            'description': 'Fictional description', 'site': source, 'date_posted': posted}


class PostingDateTests(unittest.TestCase):
    def test_only_unambiguous_calendar_dates_are_retained(self):
        self.assertEqual(posting_date(date(2025, 2, 3)), '2025-02-03')
        for value in ('yesterday', '2025-02-30', '2025-2-3', 'NaT', None,
                      '2025-02-03T12:00:00Z', datetime(2025, 2, 3, 0, 0)):
            with self.subTest(value=value): self.assertIsNone(posting_date(value))

    def test_jobspy_date_survives_source_normalization(self):
        class Frame:
            attrs = {}
            def to_dict(self, orient): return [record(date(2025, 2, 3))]
        with patch('jobspy.scrape_jobs', return_value=Frame()):
            jobs = discover_jobspy('fictional', 'United States', sites=['indeed'])
        self.assertEqual(jobs[0].reported_posted_date, '2025-02-03')
        self.assertEqual(jobs[0].posted_date_status, 'reported')

    def test_latest_source_observation_keeps_ambiguity_and_original_job(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'test.sqlite3') as store:
            first = Job.from_mapping(record('2025-02-03'))
            store.add_discovered_jobs([first])
            row = store.discovery_observations([first.id])[first.id][0]
            self.assertEqual((row['reported_posted_date'], row['posted_date_status']), ('2025-02-03', 'reported'))
            self.assertTrue(row['posted_date_observed_at'].endswith('Z'))
            store.add_discovered_jobs([first, Job.from_mapping(record(None))])
            row = store.discovery_observations([first.id])[first.id][0]
            self.assertEqual((row['reported_posted_date'], row['posted_date_status']), ('2025-02-03', 'partial'))
            store.add_discovered_jobs([first, Job.from_mapping(record('2025-03-04'))])
            row = store.discovery_observations([first.id])[first.id][0]
            self.assertEqual((row['reported_posted_date'], row['posted_date_status']), (None, 'conflicting'))
            store.add_discovered_jobs([Job.from_mapping(record('yesterday'))])
            row = store.discovery_observations([first.id])[first.id][0]
            self.assertEqual((row['reported_posted_date'], row['posted_date_status']), (None, 'missing'))
            self.assertEqual(store.get_job(first.id).description, 'Fictional description')
            self.assertEqual(row['observation_count'], 4)

    def test_metadata_does_not_change_existing_workflow_signature(self):
        job = fake_job(); profile = CandidateProfile((), (), ())
        bank = FactBank.parse(fake_fact_bank())
        from dataclasses import replace
        old = inputs(job, profile, bank, ())
        reported = inputs(replace(job, reported_posted_date='2025-02-03', posted_date_status='reported'), profile, bank, ())
        self.assertNotIn('reported_posted_date', old['job'])
        self.assertEqual(signature(old), signature(reported))
