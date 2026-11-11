"""Offline schedule, crash recovery, idempotency and durable approval tests."""
import json
import tempfile
import time
import unittest
import subprocess
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from career_agent.automation import Automation
from career_agent.candidate import FactBank
from career_agent.models import CandidateProfile
from career_agent.storage import Store
from career_agent.workflows import Workflows, inputs, signature
from test_dashboard import fake_job, fake_fact_bank


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.sqlite3'
        self.auto = Automation(self.db)
        self.search = {'query': 'Fictional engineer', 'location': 'United States', 'sites': ['indeed'],
                       'interval_minutes': 60, 'results': 5, 'acknowledged': True}

    def test_schedule_requires_consent_and_valid_bounded_filters(self):
        for changes in ({'acknowledged': False}, {'interval_minutes': 1}, {'results': True}, {'sites': []}, {'sites': ['unknown']}, {'query': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.auto.save(self.search | changes)
        self.auto.save(self.search)
        with self.assertRaises(ValueError):
            self.auto.save(self.search)

    def test_scheduled_import_deduplicates_and_survives_restart(self):
        self.auto.save(self.search)
        job = asdict(fake_job())
        with patch('career_agent.automation.fetch', return_value=[job, job]) as fetch:
            self.assertTrue(self.auto.tick())
            self.assertFalse(self.auto.tick())
            fetch.assert_called_once()
        second = Automation(self.db)
        due = second.list()['schedules'][0]['next_run']
        with patch('career_agent.automation.fetch', return_value=[job]):
            self.assertTrue(second.tick(now=due + 1))
        rows = second.list()['runs']
        self.assertEqual([r['added'] for r in rows], [0, 1])
        with Store(self.db) as store:
            self.assertEqual(len(store.list_jobs()), 1)

    def test_pause_during_fetch_discards_results_and_lease_blocks_second_worker(self):
        sid = self.auto.save(self.search)['id']
        def fetch(settings, stop):
            self.assertFalse(Automation(self.db).tick())
            self.auto.enable(sid, False)
            return [asdict(fake_job())]
        with patch('career_agent.automation.fetch', side_effect=fetch):
            self.auto.tick()
        with Store(self.db) as store:
            self.assertEqual(store.list_jobs(), [])
        self.assertEqual(self.auto.list()['runs'][0]['status'], 'failed')

    def test_empty_and_failed_sources_are_not_zero_vacancy_claims(self):
        self.auto.save(self.search)
        with patch('career_agent.automation.fetch', return_value=[]):
            self.auto.tick()
        self.assertEqual(self.auto.list()['runs'][0]['status'], 'inconclusive')
        due = self.auto.list()['schedules'][0]['next_run']
        with patch('career_agent.automation.fetch', side_effect=RuntimeError('provider private detail')):
            self.auto.tick(now=due + 1)
        report = self.auto.list()
        self.assertEqual(report['runs'][0]['status'], 'failed')
        self.assertNotIn('provider private detail', json.dumps(report))
        self.assertEqual(report['schedules'][0]['failures'], 2)

    def test_expired_lease_records_interrupted_run(self):
        sid = self.auto.save(self.search)['id']
        with Store(self.db) as store, store.connection:
            store.connection.execute("INSERT INTO discovery_runs(id,schedule_id,status,started) VALUES('dead',?,'running',?)", (sid,time.time()-400))
            store.connection.execute('UPDATE discovery_schedules SET next_run=?', (time.time()+9999,))
        with patch('career_agent.automation.fetch', return_value=[]):
            self.auto.tick()
        self.assertEqual(next(r for r in self.auto.list()['runs'] if r['id'] == 'dead')['status'], 'interrupted')

    def test_stalled_child_is_killed_at_deadline(self):
        from career_agent.automation import fetch
        with patch('career_agent.automation.subprocess.Popen') as popen, patch('career_agent.automation.time.monotonic', side_effect=[0, 0, 121]):
            child = popen.return_value.__enter__.return_value
            child.communicate.side_effect = [subprocess.TimeoutExpired('fictional', 1), ('', None)]
            with self.assertRaises(ValueError):
                fetch(self.search, self.auto.stop)
            child.kill.assert_called_once()


class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db, self.packets = self.root / 'test.sqlite3', self.root / 'packets'
        self.job = fake_job()
        with Store(self.db) as store:
            store.add_jobs([self.job])
        self.state = inputs(self.job, CandidateProfile(('AI Lead',), ('Python',), (), True), FactBank.parse(fake_fact_bank()), (), 1)
        self.manager = Workflows(self.db, self.packets)

    def test_review_survives_restart_and_does_not_redraft_or_duplicate(self):
        run = self.manager.start(self.state)
        self.assertEqual(run['status'], 'awaiting_human_review')
        restarted = Workflows(self.db, self.packets)
        decision = {'approved': True, 'reviewed': True, 'packet_sha256': run['packet']['packet_sha256']}
        with patch('career_agent.graph_review.build_packet', side_effect=AssertionError('Must not redraft')):
            approved = restarted.advance(run['run_id'], signature(self.state), decision)
            restarted.advance(run['run_id'], signature(self.state), decision)
        self.assertEqual(approved['status'], 'approved_for_manual_submission')
        with Store(self.db) as store:
            self.assertEqual(len(store.review_history()), 1)
            self.assertEqual(store.stage_of(self.job.id), 'discovered')

    def test_failure_resumes_from_checkpoint(self):
        with patch('career_agent.graph_review.build_packet', side_effect=ValueError('injected failure')), self.assertRaises(ValueError):
            self.manager.start(self.state)
        row = self.manager.list()[0]
        self.assertEqual(row['status'], 'failed')
        recovered = Workflows(self.db, self.packets).advance(row['run_id'], signature(self.state))
        self.assertEqual(recovered['status'], 'awaiting_human_review')

    def test_changed_inputs_or_obsolete_run_cannot_replace_newer_draft(self):
        first = self.manager.start(self.state)
        with self.assertRaises(ValueError):
            self.manager.advance(first['run_id'], signature(self.state | {'workspace_revision': 2}))
        second = self.manager.start(self.state)
        with self.assertRaises(ValueError):
            self.manager.advance(first['run_id'], signature(self.state))
        with self.assertRaises(ValueError):
            self.manager.advance(second['run_id'], signature(self.state), {'packet_sha256': 'a'*64, 'approved': True, 'reviewed': True})

    def test_explicit_changed_review_uses_new_interrupt(self):
        run = self.manager.start(self.state)
        for approved in (False, True):
            self.manager.advance(run['run_id'], signature(self.state), {'packet_sha256': run['packet']['packet_sha256'], 'approved': approved, 'reviewed': True})
        with Store(self.db) as store:
            self.assertEqual(len(store.review_history()), 2)
            self.assertEqual(store.latest_review(self.job.id)['decision'], 'approved_for_manual_submission')

    def test_incomplete_publication_cannot_fall_back_to_legacy_review(self):
        run = self.manager.start(self.state)
        with Store(self.db) as store, store.connection:
            store.connection.execute('UPDATE workflow_runs SET packet_sha256=NULL,status=\'queued\' WHERE run_id=?', (run['run_id'],))
        with self.assertRaises(ValueError):
            self.manager.review(self.job.id, run['packet']['packet_sha256'], True, self.state)
        recovered = self.manager.advance(run['run_id'], signature(self.state))
        self.assertEqual(recovered['status'], 'awaiting_human_review')
