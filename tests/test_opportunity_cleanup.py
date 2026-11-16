import json
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from unittest.mock import patch
import test_dashboard
from career_agent.storage import Store
from career_agent.posting_checks import PostingChecks


@unittest.skipUnless(test_dashboard.HAS_WEB, 'Install web extras')
class OpportunityCleanupTests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    call = test_dashboard.DashboardTests.call

    def archive(self, job, reason='confirmed_closed', **extra):
        return self.call('POST', f'/api/jobs/{job.id}/archive', {'acknowledged': True, 'reason': reason, **extra})

    def test_duplicate_preserves_history_reimport_and_restore(self):
        other = replace(self.job, url='https://example.invalid/duplicate')
        with Store(self.db) as store:
            store.add_jobs([other]); store.stage(other.id)
            store.record_review(other.id, 'a' * 64, False)
        self.assertEqual(self.call('POST', f'/api/jobs/{other.id}/draft', {}).status_code, 200)
        packet = self.call('GET', f'/api/jobs/{other.id}/packet').json()
        result = self.archive(other, 'duplicate', duplicate_of=self.job.id)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['archive']['duplicate_of'], self.job.id)
        self.assertEqual(self.call('GET', f'/api/jobs/{other.id}/packet').json(), packet)
        self.assertEqual([j['id'] for j in self.call('GET', '/api/jobs').json()['jobs']], [self.job.id])
        rows = self.call('GET', '/api/jobs?include_archived=true').json()['jobs']
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.call('GET', '/api/h1b/opportunities').json()['jobs']), 1)
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(other.id), 'ready_for_review')
            self.assertEqual(len(store.review_history()), 1)
            self.assertEqual(store.add_discovered_jobs([other]), 0)
            self.assertIsNotNone(store.archive_state(other.id))
            self.assertEqual(len(store.list_jobs()), 1)
        self.assertEqual(self.call('POST', f'/api/jobs/{other.id}/restore', {'acknowledged': True}).status_code, 200)
        self.assertEqual(len(self.call('GET', '/api/jobs').json()['jobs']), 2)
        self.assertEqual(self.call('GET', '/api/overview').json()['archived_jobs'], 0)

    def test_duplicate_requires_distinct_active_matching_identity_and_confirmation(self):
        other = replace(self.job, url='https://example.invalid/other', title='Different requisition')
        with Store(self.db) as store: store.add_jobs([other])
        for parent in (self.job.id, other.id, 'missing'):
            self.assertEqual(self.archive(self.job, 'duplicate', duplicate_of=parent).status_code, 422)
        other = replace(other, title=self.job.title)
        with Store(self.db) as store, store.connection:
            store.connection.execute('UPDATE jobs SET title=? WHERE job_id=?', (other.title, other.id))
        self.assertEqual(self.archive(other).status_code, 200)
        self.assertEqual(self.archive(self.job, 'duplicate', duplicate_of=other.id).status_code, 422)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/archive', {'reason': 'confirmed_closed'}).status_code, 422)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/archive', {'reason': 'confirmed_closed', 'acknowledged': True}, token='bad').status_code, 403)
        self.assertEqual(self.archive(self.job, 'expired').status_code, 422)
        self.assertEqual(self.archive(self.job, reason=['duplicate']).status_code, 422)

    def expired(self):
        now = datetime.now(timezone.utc)
        return {'availability': 'expired', 'http_status': 200, 'provider': 'website',
                'source_url': self.job.url, 'checked_at': now.isoformat(),
                'application_deadline': (now - timedelta(days=1)).isoformat(), 'reason': 'Deadline passed'}

    def test_cleanup_rejects_uncertain_stale_and_replaced_observations(self):
        now = datetime.now(timezone.utc)
        variants = [{'availability': 'unknown', 'http_status': status} for status in (403, 404, 410, 429, 500)]
        variants += [{'availability': 'page_reachable'}, {'availability': 'listed'},
                     {'checked_at': (now - timedelta(days=2)).isoformat()},
                     {'checked_at': (now + timedelta(days=2)).isoformat()},
                     {'application_deadline': None}, {'application_deadline': '2020-01-01'},
                     {'application_deadline': (now + timedelta(days=1)).isoformat()},
                     {'source_url': 'https://example.invalid/another'}]
        for change in variants:
            with self.subTest(change=change):
                with Store(self.db) as store:
                    checks = PostingChecks(store)
                    checks.save(self.job.id, self.expired())
                    checks.save(self.job.id, {**self.expired(), **change})
                result = self.call('POST', '/api/opportunities/cleanup', {'acknowledged': True})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['archived'], 0)
        for value in (False, 1, 'true', None):
            self.assertEqual(self.call('POST', '/api/opportunities/cleanup', {'acknowledged': value}).status_code, 422)
            self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/restore', {'acknowledged': value}).status_code, 422)

    def test_cleanup_is_idempotent_and_restore_persists_until_another_check_or_cleanup(self):
        with Store(self.db) as store: PostingChecks(store).save(self.job.id, self.expired())
        for expected in (1, 0):
            self.assertEqual(self.call('POST', '/api/opportunities/cleanup', {'acknowledged': True}).json()['archived'], expected)
        archived = self.call('GET', '/api/jobs?include_archived=true').json()['jobs'][0]['archive']
        self.assertEqual(archived['evidence']['http_status'], 200)
        self.call('POST', f'/api/jobs/{self.job.id}/restore', {'acknowledged': True})
        for _ in range(2): self.assertEqual(len(self.call('GET', '/api/jobs').json()['jobs']), 1)

    def test_live_website_check_archives_only_identity_matched_expiry(self):
        job = replace(self.job, url='https://careers.example.com/jobs/123')
        with Store(self.db) as store: store.add_jobs([job])
        body = {'@type': 'JobPosting', 'url': job.url, 'title': job.title,
                'hiringOrganization': {'name': job.company}, 'description': job.description,
                'validThrough': '2020-01-01T00:00:00Z'}
        def response():
            return (200, ('<script type="application/ld+json">' + json.dumps(body) + '</script>').encode(), 'text/html')
        with patch('career_agent.posting_checks.public_get', return_value=response()):
            result = self.call('POST', f'/api/jobs/{job.id}/live-check', {'acknowledged': True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['archive']['reason'], 'expired')
        self.call('POST', f'/api/jobs/{job.id}/restore', {'acknowledged': True})
        body['title'] = 'Unrelated role'
        with patch('career_agent.posting_checks.public_get', return_value=response()):
            result = self.call('POST', f'/api/jobs/{job.id}/live-check', {'acknowledged': True})
        self.assertIsNone(result.json()['archive'])

    def test_pagination_and_duplicate_candidates_exclude_archives(self):
        jobs = [replace(self.job, url=f'https://example.invalid/{i}') for i in range(5)]
        with Store(self.db) as store:
            store.add_jobs(jobs)
            with store.connection:
                for job in jobs[::2]: store.archive_job(job.id, 'confirmed_closed')
            page, cursor = store.job_page(limit=2)
            second, cursor2 = store.job_page(limit=2, before=cursor)
            self.assertEqual([j.id for j in page + second], [jobs[3].id, jobs[1].id, self.job.id])
            self.assertIsNone(cursor2)
            self.assertEqual({j['job_id'] for j in store.possible_duplicates(self.job.id)['jobs']}, {jobs[1].id, jobs[3].id})
