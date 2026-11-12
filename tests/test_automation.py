"""Offline schedule, crash recovery, idempotency and durable approval tests."""
import json
import logging
import tempfile
import time
import unittest
import subprocess
import sys
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from career_agent.automation import Automation
from career_agent.candidate import FactBank
from career_agent.discovery import DiscoveryResults
from career_agent.models import CandidateProfile, Job
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
            observations = store.discovery_observations([fake_job().id])[fake_job().id]
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]['observation_count'], 2)
            self.assertTrue(observations[0]['first_seen'].endswith('Z'))
            self.assertGreaterEqual(observations[0]['last_seen'], observations[0]['first_seen'])

    def test_discovery_keeps_distinct_sources_and_original_job_data(self):
        self.auto.save(self.search)
        original = fake_job()
        with Store(self.db) as store:
            store.add_jobs([original])
        first = asdict(original) | {'source': 'indeed', 'description': 'Changed by source'}
        second = asdict(original) | {'source': 'linkedin', 'description': 'Another source'}
        with patch('career_agent.automation.fetch', return_value=[first, first, second]):
            self.assertTrue(self.auto.tick())
        with Store(self.db) as store:
            self.assertEqual(store.get_job(original.id).description, original.description)
            rows = store.discovery_observations([original.id])[original.id]
            self.assertEqual({(r['source'], r['observation_count']) for r in rows}, {('indeed', 1), ('linkedin', 1)})
            self.assertEqual({r['description_comparison'] for r in rows}, {'differs'})
            self.assertEqual(len(store.list_jobs()), 1)

    def test_discovery_description_comparison_preserves_saved_text_and_reviews(self):
        job = replace(fake_job(), source='indeed', description='Original Python skills.')
        with Store(self.db) as store:
            self.assertEqual(store.add_discovered_jobs([job]), 1)
            store.record_review(job.id, 'a' * 64, True)
            status = lambda: store.discovery_observations([job.id])[job.id][0]
            self.assertEqual(status()['description_comparison'], 'matches')
            self.assertTrue(status()['description_observed_at'].endswith('Z'))
            self.assertEqual(store.add_discovered_jobs([replace(job, description='  ORIGINAL\nPython skills. ')]), 0)
            self.assertEqual(status()['description_comparison'], 'matches')
            self.assertEqual(store.add_discovered_jobs([replace(job, description='New SQL skills.')]), 0)
            self.assertEqual(status()['description_comparison'], 'differs')
            self.assertEqual(store.get_job(job.id).description, 'Original Python skills.')
            self.assertEqual(store.latest_review(job.id)['decision'], 'approved_for_manual_submission')
            store.correct_description(job.id, 'New SQL skills.', 0, 'Fictional source checked')
            self.assertEqual(status()['description_comparison'], 'matches')
            store.add_discovered_jobs([replace(job, description='')])
            self.assertEqual(status()['description_comparison'], 'unknown')
            self.assertEqual(store.get_job(job.id).description, 'New SQL skills.')

    def test_legacy_observations_have_unknown_description_comparison(self):
        job = fake_job()
        with Store(self.db) as store, store.connection:
            store.add_jobs([job])
            store.connection.execute('INSERT INTO job_discovery_observations VALUES(?,?,?,?,?)',
                                     (job.id, job.source, '2025-01-01Z', '2025-01-01Z', 1))
            observation = store.discovery_observations([job.id])[job.id][0]
            self.assertEqual(observation['description_comparison'], 'unknown')
            self.assertIsNone(observation['description_observed_at'])

    def test_failed_discovery_import_rolls_back_jobs_and_observations(self):
        job = fake_job()
        malformed = Job(company='Fictional', title='Role', location='', url='not a URL')
        with Store(self.db) as store:
            with self.assertRaises(ValueError):
                store.add_discovered_jobs([job, malformed])
            self.assertEqual(store.list_jobs(), [])
            self.assertEqual(store.discovery_observations([job.id]), {})

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
            self.assertEqual(store.discovery_observations([fake_job().id]), {})
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

    def test_partial_source_failure_survives_import_without_provider_details(self):
        search = self.search | {'sites': ['indeed', 'linkedin']}
        self.auto.save(search)
        statuses = {'indeed': {'state': 'returned', 'returned': 1},
                    'linkedin': {'state': 'reported_error', 'returned': 0}}
        with patch('career_agent.automation.fetch', return_value=DiscoveryResults([asdict(fake_job())], statuses)):
            self.assertTrue(self.auto.tick())
        result = self.auto.list()
        self.assertEqual(result['runs'][0]['status'], 'partial')
        self.assertIn('linkedin: reported_error (0)', result['runs'][0]['message'])
        self.assertEqual(result['schedules'][0]['failures'], 1)
        with Store(self.db) as store:
            self.assertEqual(len(store.list_jobs()), 1)
            receipt = json.loads(store.connection.execute('SELECT counts FROM discovery_imports').fetchone()['counts'])
        self.assertEqual(receipt['source_status'], statuses)

    def test_recovery_preserves_partial_source_status(self):
        sid = self.auto.save(self.search)['id']
        self.auto.enable(sid, False)
        counts = {'found': 1, 'added': 1, 'source_status': {
            'indeed': {'state': 'reported_warning', 'returned': 1}}}
        with Store(self.db) as store, store.connection:
            store.connection.execute("INSERT INTO discovery_runs(id,schedule_id,status,started) VALUES('dead',?,'running',?)", (sid, time.time()-400))
            store.connection.execute('INSERT INTO discovery_imports(run_id,counts) VALUES(?,?)', ('dead', json.dumps(counts)))
        self.assertFalse(self.auto.tick())
        recovered = self.auto.list()['runs'][0]
        self.assertEqual(recovered['status'], 'partial')
        self.assertIn('reported_warning', recovered['message'])

    def test_jobspy_metadata_is_sanitized_and_empty_is_inconclusive(self):
        from career_agent.discovery import discover_jobspy
        class Frame:
            attrs = {'errors': {'linkedin': 'fictional private provider detail'},
                     'warnings': {'zip_recruiter': ['fictional private warning']}}
            def to_dict(self, orient):
                return [asdict(fake_job()) | {'source': 'indeed'}]
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=lambda **kwargs: Frame())}):
            results = discover_jobspy('Fictional role', 'USA', 3, sites=['indeed', 'linkedin', 'zip_recruiter'])
        self.assertEqual(results.source_status['indeed'], {'state': 'returned', 'returned': 1})
        self.assertEqual(results.source_status['linkedin'], {'state': 'reported_error', 'returned': 0})
        self.assertEqual(results.source_status['zip_recruiter'], {'state': 'reported_warning', 'returned': 0})
        self.assertNotIn('fictional private', json.dumps(results.source_status))
        Frame.attrs = {}
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=lambda **kwargs: Frame())}):
            results = discover_jobspy('Fictional role', 'USA', 3, sites=['indeed', 'linkedin'])
        self.assertEqual(results.source_status['linkedin']['state'], 'inconclusive')

    def test_jobspy_logged_skips_are_reported_without_provider_text(self):
        from career_agent.discovery import discover_jobspy
        logger = logging.getLogger('jobspy.ziprecruiter')
        class Frame:
            attrs = {}
            def to_dict(self, orient):
                return [asdict(fake_job()) | {'source': 'zip_recruiter'}]
        def scrape(**_):
            logger.warning('skipping job: fictional private provider detail')
            return Frame()
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=scrape)}), self.assertLogs(logger, level='WARNING'):
            results = discover_jobspy('Fictional role', 'USA', 3, sites=['zip_recruiter'])
        self.assertEqual(results.source_status['zip_recruiter'], {'state': 'reported_warning', 'returned': 1})
        self.assertNotIn('fictional private', json.dumps(results.source_status))
        self.assertEqual(len(results), 1)
        before = tuple(logger.handlers)
        def fail(**_):
            raise RuntimeError('fictional failure')
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=fail)}), self.assertRaises(RuntimeError):
            discover_jobspy('Fictional role', 'USA', 3, sites=['zip_recruiter'])
        self.assertEqual(tuple(logger.handlers), before)

    def test_expired_lease_records_interrupted_run(self):
        sid = self.auto.save(self.search)['id']
        with Store(self.db) as store, store.connection:
            store.connection.execute("INSERT INTO discovery_runs(id,schedule_id,status,started) VALUES('dead',?,'running',?)", (sid,time.time()-400))
            store.connection.execute('UPDATE discovery_schedules SET next_run=?', (time.time()+9999,))
        with patch('career_agent.automation.fetch', return_value=[]):
            self.auto.tick()
        self.assertEqual(next(r for r in self.auto.list()['runs'] if r['id'] == 'dead')['status'], 'interrupted')

    def test_stalled_child_is_killed_at_deadline(self):
        from career_agent.automation import fetch, DiscoveryTimedOut
        with patch('career_agent.automation.subprocess.Popen') as popen, patch('career_agent.automation.time.monotonic', side_effect=[0, 0, 121]):
            child = popen.return_value.__enter__.return_value
            child.communicate.side_effect = [subprocess.TimeoutExpired('fictional', 1), ('', None)]
            with self.assertRaises(DiscoveryTimedOut):
                fetch(self.search, self.auto.stop)
            child.kill.assert_called_once()

    def test_worker_source_status_is_validated(self):
        from career_agent.automation import fetch
        payload = {'rows': [], 'source_status': {'indeed': {'state': 'inconclusive', 'returned': 0}}}
        with patch('career_agent.automation.subprocess.Popen') as popen:
            child = popen.return_value.__enter__.return_value
            child.returncode = 0
            child.communicate.return_value = (json.dumps(payload), None)
            result = fetch(self.search, self.auto.stop)
            self.assertEqual(result.source_status, payload['source_status'])
            payload['source_status']['indeed']['raw_error'] = 'secret'
            child.communicate.return_value = (json.dumps(payload), None)
            with self.assertRaisesRegex(ValueError, 'Invalid discovery source status'):
                fetch(self.search, self.auto.stop)

    def test_stop_kills_isolated_discovery_before_import(self):
        from career_agent.automation import fetch, DiscoveryStopped
        self.auto.stop.set()
        with patch('career_agent.automation.subprocess.Popen') as popen:
            child = popen.return_value.__enter__.return_value
            with self.assertRaises(DiscoveryStopped):
                fetch(self.search, self.auto.stop)
            child.kill.assert_called_once()

    def test_manual_discovery_uses_bounded_worker_and_normalizes_results(self):
        from dataclasses import asdict
        from career_agent.discovery import discover_isolated, DiscoveryUnavailable
        job = fake_job('https://example.invalid/fictional-manual')
        with patch('career_agent.discovery.importlib.util.find_spec', return_value=object()), \
             patch('career_agent.automation.fetch', return_value=[asdict(job)]) as worker:
            result = discover_isolated('Fictional role', 'USA', 5, self.auto.stop)
        self.assertEqual(result, [job])
        self.assertEqual(worker.call_args.args[0], {
            'query': 'Fictional role', 'location': 'USA', 'results': 5,
            'sites': ['indeed', 'linkedin', 'zip_recruiter']})
        self.assertIs(worker.call_args.args[1], self.auto.stop)
        with patch('career_agent.discovery.importlib.util.find_spec', return_value=None), \
             patch('career_agent.automation.fetch') as worker:
            with self.assertRaises(DiscoveryUnavailable):
                discover_isolated('Fictional role', 'USA', 5, self.auto.stop)
            worker.assert_not_called()


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

    def test_start_does_not_insert_a_run_after_submission(self):
        with Store(self.db) as store, store.connection:
            store.connection.execute("INSERT INTO applications(job_id,stage,receipt) VALUES(?,'submitted','fictional')", (self.job.id,))
        with self.assertRaisesRegex(ValueError, 'Submitted application is locked'):
            self.manager.start(self.state)
        self.assertEqual(self.manager.list(), [])
        self.assertFalse(self.packets.exists())

    def test_historical_packets_are_exact_and_read_only(self):
        import copy
        first = self.manager.start(self.state)
        updated = copy.deepcopy(self.state)
        updated['fact_bank']['facts'][0]['statement'] = 'A different fictional verified career statement.'
        second = self.manager.start(updated)
        before = self.manager.list()
        versions = self.manager.versions(self.job.id)
        self.assertEqual([v['run_id'] for v in versions], [second['run_id'], first['run_id']])
        old = self.manager.historical_packet(self.job.id, first['run_id'])
        self.assertEqual(old['packet_sha256'], first['packet']['packet_sha256'])
        self.assertEqual(old['resume_markdown'], first['packet']['resume_markdown'])
        self.assertNotIn('A different fictional verified career statement.', old['resume_markdown'])
        self.assertNotIn('Private fictional evidence', json.dumps(old))
        self.assertEqual(self.manager.list(), before)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.manager.historical_packet('different-job', first['run_id'])
        with self.assertRaises(ValueError):
            self.manager.historical_packet(self.job.id, '../invalid')
        with Store(self.db) as store, store.connection:
            store.connection.execute('UPDATE workflow_runs SET packet_sha256=? WHERE run_id=?', ('0' * 64, first['run_id']))
        with self.assertRaisesRegex(ValueError, 'unavailable or invalid'):
            self.manager.historical_packet(self.job.id, first['run_id'])
        Path(self.manager.checkpoints).unlink()
        with self.assertRaisesRegex(ValueError, 'checkpoint is unavailable'):
            self.manager.historical_packet(self.job.id, first['run_id'])
        self.assertFalse(Path(self.manager.checkpoints).exists())

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
            self.manager.review(self.job.id, run['packet']['packet_sha256'], True, lambda: self.state)
        recovered = self.manager.advance(run['run_id'], signature(self.state))
        self.assertEqual(recovered['status'], 'awaiting_human_review')

    def test_cli_can_leave_review_pending_and_resume_later(self):
        from career_agent.cli import main
        profile, facts = self.root / 'profile.json', self.root / 'facts.json'
        profile.write_text(json.dumps(self.state['preferences']))
        facts.write_text(json.dumps(self.state['fact_bank']))
        args = ['--db', str(self.db), 'workflow', self.job.id, '--profile', str(profile),
                '--facts', str(facts), '--output', str(self.packets / (self.job.id + '.json'))]
        with patch('builtins.input', return_value=''), redirect_stdout(StringIO()):
            self.assertEqual(main(args), 0)
        run = self.manager.list()[0]
        self.assertEqual(run['status'], 'awaiting_human_review')
        with patch('builtins.input', return_value='APPROVE ' + self.job.id), redirect_stdout(StringIO()):
            self.assertEqual(main(args + ['--resume-run', run['run_id']]), 0)
        self.assertEqual(self.manager.list()[0]['status'], 'approved_for_manual_submission')
