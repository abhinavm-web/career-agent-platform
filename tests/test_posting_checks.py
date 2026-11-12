"""Exact ATS identities, conservative availability and non-mutating live observations."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from career_agent.models import Job
from career_agent.posting_checks import PostingChecks, check_posting, target
from career_agent.public_sources import SourceUnavailable
from career_agent.storage import Store


def job(url='https://boards.greenhouse.io/example/jobs/123'):
    return Job.from_mapping({'company': 'Fictional Corp', 'title': 'Engineer', 'job_url': url, 'description': 'Saved original text'})


def payload(**changes):
    return {'id': 123, 'title': 'Engineer', 'company_name': 'Fictional Corp', 'internal_job_id': 456,
            'content': '<p>H-1B sponsorship available.</p>', **changes}


class PostingTests(unittest.TestCase):
    def checked(self, data, sample=None):
        with patch('career_agent.posting_checks.public_get', return_value=(200, json.dumps(data).encode(), 'application/json')):
            return check_posting(sample or job())

    def test_only_exact_public_provider_paths_are_contacted(self):
        for url in ('https://127.0.0.1/jobs/123', 'https://boards.greenhouse.io.evil.invalid/example/jobs/123',
                    'https://user:pass@boards.greenhouse.io/example/jobs/123', 'https://boards.greenhouse.io/example/../jobs/123',
                    'http://boards.greenhouse.io/example/jobs/123', 'https://example.invalid/job'):
            with self.subTest(url=url), patch('career_agent.posting_checks.public_get') as fetch:
                self.assertIsNone(target(url))
                # Job constructor rejects embedded credentials independently.
                if '@' not in url:
                    self.assertEqual(check_posting(job(url))['availability'], 'unknown')
                fetch.assert_not_called()
        self.assertIn('api.eu.lever.co', target('https://jobs.eu.lever.co/example/11111111-2222-3333-4444-555555555555')[2])

    def test_listed_identity_deadlines_and_prospect_posts(self):
        checked = self.checked(payload())
        self.assertEqual(checked['availability'], 'listed')
        self.assertEqual(checked['policy']['policy'], 'possible')
        self.assertEqual(len(checked['content_sha256']), 64)
        self.assertEqual(self.checked(payload(internal_job_id=None))['availability'], 'general_interest')
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertEqual(self.checked(payload(application_deadline=past))['availability'], 'expired')
        self.assertEqual(self.checked(payload(application_deadline=future))['availability'], 'listed')
        for changes in ({'id': 999}, {'title': 'Different role'}, {'company_name': 'Subsidiary'},
                        {'company_name': []}, {'internal_job_id': 'bad'}, {'content': ''},
                        {'application_deadline': 'bad'}, {'application_deadline': '2025-01-01'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.checked(payload(**changes))['availability'], 'unknown')

    def test_lever_additional_text_is_part_of_policy(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        data = {'id': identifier, 'text': 'Engineer', 'descriptionPlain': 'Build software.',
                'lists': [{'text': 'Requirements', 'content': 'Python'}], 'additionalPlain': 'No visa sponsorship.'}
        checked = self.checked(data, job('https://jobs.lever.co/example/' + identifier))
        self.assertEqual(checked['availability'], 'listed')
        self.assertEqual(checked['policy']['policy'], 'excluded')

    def test_live_posting_does_not_present_scoped_support_as_role_commitment(self):
        text = '<p>Visa sponsorship is available for other roles.</p>'
        checked = self.checked(payload(content=text))
        self.assertEqual(checked['availability'], 'listed')
        self.assertEqual(checked['policy']['policy'], 'conditional')
        self.assertIn('Visa sponsorship is available for other roles.', checked['policy']['quotes'])
        self.assertNotIn('potential_match', checked['policy'])

    def test_errors_redirects_and_removed_urls_remain_unknown(self):
        for response in ((403, b'', ''), (404, b'', ''), (429, b'', ''), (302, b'', ''),
                         (200, b'<html>captcha</html>', 'text/html'), (200, b'{}', 'application/json')):
            with self.subTest(response=response), patch('career_agent.posting_checks.public_get', return_value=response):
                self.assertEqual(check_posting(job())['availability'], 'unknown')
        with patch('career_agent.posting_checks.public_get', side_effect=SourceUnavailable('Timed out')):
            self.assertEqual(check_posting(job())['availability'], 'unknown')

    def test_latest_failed_observation_does_not_reuse_a_previous_positive(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'test.db') as store:
            sample = job()
            store.add_jobs([sample])
            checks = PostingChecks(store)
            good = self.checked(payload())
            good['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            checks.save(sample.id, good)
            self.assertTrue(checks.latest(sample.id)['stale'])
            self.assertEqual(checks.latest(sample.id)['availability'], 'unknown')
            self.assertEqual(checks.latest(sample.id)['observed_availability'], 'listed')
            checks.save(sample.id, {**good, 'availability': 'unknown', 'policy': None, 'checked_at': datetime.now(timezone.utc).isoformat()})
            self.assertEqual(checks.latest(sample.id)['availability'], 'unknown')
            self.assertEqual(store.get_job(sample.id).description, 'Saved original text')
            self.assertEqual(store.stage_of(sample.id), 'discovered')
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM posting_checks').fetchone()[0], 2)

    def test_invalid_naive_and_future_timestamps_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'test.db') as store:
            sample = job(); store.add_jobs([sample]); checks = PostingChecks(store)
            for timestamp in ('bad', None, '2025-01-01T12:00:00',
                              (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
                with self.subTest(timestamp=timestamp):
                    checks.save(sample.id, {'availability': 'listed', 'checked_at': timestamp, 'reason': 'Fictional observation'})
                    current = checks.latest(sample.id)
                    self.assertTrue(current['stale'])
                    self.assertEqual(current['availability'], 'unknown')
                    self.assertEqual(current['observed_availability'], 'listed')

    def test_future_deadline_is_rechecked_when_read_without_changing_audit_record(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'test.db') as store:
            sample = job(); store.add_jobs([sample]); checks = PostingChecks(store)
            current_time = datetime.now(timezone.utc)
            observation = {'availability': 'listed', 'checked_at': (current_time - timedelta(hours=1)).isoformat(),
                           'application_deadline': (current_time - timedelta(minutes=1)).isoformat(),
                           'reason': 'Listed at check time'}
            checks.save(sample.id, observation)
            current = checks.latest(sample.id)
            self.assertFalse(current['stale'])
            self.assertEqual(current['availability'], 'expired')
            self.assertEqual(current['observed_availability'], 'listed')
            self.assertIn('Current availability is unknown', current['reason'])
            stored = json.loads(store.connection.execute('SELECT result_json FROM posting_checks').fetchone()[0])
            self.assertEqual(stored, observation)
            for invalid in ('bad', '2026-01-01', '2026-01-01T12:00:00'):
                with self.subTest(deadline=invalid):
                    checks.save(sample.id, {**observation, 'application_deadline': invalid})
                    self.assertEqual(checks.latest(sample.id)['availability'], 'unknown')
            checks.save(sample.id, {**observation, 'checked_at': (current_time - timedelta(days=2)).isoformat()})
            self.assertTrue(checks.latest(sample.id)['stale'])
            self.assertEqual(checks.latest(sample.id)['availability'], 'unknown')
