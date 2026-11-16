import json
import unittest
from unittest.mock import patch
import test_dashboard
from career_agent import ai
from career_agent.storage import Store

ROUTE = {'provider': 'anthropic', 'model': 'fictional-model', 'max_tokens': 4096}
OUTPUT = {'resume': [{'text': 'Delivered an AI program using Python and AWS.', 'fact_ids': ['experience-01']}],
          'cover_letter': [{'text': 'My experience delivering an AI program with Python and AWS is relevant to this role.', 'fact_ids': ['experience-01']}]}

class DocumentRevisionTests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    call = test_dashboard.DashboardTests.call

    def request(self, parent=0):
        return {'route': 'claude', 'route_fingerprint': ai.route_fingerprint('claude', ROUTE),
                'instructions': 'Make it concise.', 'parent_id': parent, 'acknowledged': True}

    def endpoint(self):
        return '/api/jobs/' + self.job.id + '/document-revisions'

    def test_generate_review_export_restart_and_regenerate(self):
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)) as complete:
            path = self.endpoint()
            result = self.call('POST', path, self.request())
            self.assertEqual(result.status_code, 200, result.text)
            revision = result.json()['revisions'][0]
            self.assertEqual(revision['document']['resume'][0]['text'], OUTPUT['resume'][0]['text'])
            self.assertIsNone(revision['reviewed_at'])
            self.assertNotIn('Private fictional evidence', complete.call_args.args[1])
            self.assertNotIn('Fictional Candidate', complete.call_args.args[1])
            rid = revision['id']
            self.assertEqual(self.call('GET', path + f'/{rid}/resume.txt').status_code, 422)
            self.assertEqual(self.call('POST', path + f'/{rid}/review', {'acknowledged': False}).status_code, 422)
            self.assertEqual(self.call('POST', path + f'/{rid}/review', {'acknowledged': True}).status_code, 200)
            exported = self.call('GET', path + f'/{rid}/cover_letter.txt')
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertIn('Dear Hiring Team', exported.text)
            self.assertEqual(self.call('GET', path).json()['revisions'][0]['id'], rid)
            with Store(self.db) as store:
                self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM ai_document_revisions').fetchone()[0], 1)
                self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM packet_reviews').fetchone()[0], 0)
            newer = self.call('POST', path, self.request(rid))
            self.assertEqual(newer.status_code, 200, newer.text)
            self.assertIsNone(newer.json()['revisions'][0]['reviewed_at'])
            self.assertEqual(self.call('GET', path + f'/{rid}/resume.txt').status_code, 422)

    def test_consent_bad_citations_and_changed_sources_fail_closed(self):
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)) as complete:
            path = self.endpoint()
            bad = {**self.request(), 'acknowledged': False}
            self.assertEqual(self.call('POST', path, bad).status_code, 422); complete.assert_not_called()
            bad = {**self.request(), 'route_fingerprint': 'changed'}
            self.assertEqual(self.call('POST', path, bad).status_code, 422); complete.assert_not_called()
            complete.return_value = json.dumps({'resume': [{'text': 'Invented', 'fact_ids': ['missing']}], 'cover_letter': OUTPUT['cover_letter']})
            self.assertEqual(self.call('POST', path, self.request()).status_code, 422)
            self.assertEqual(self.call('GET', path).json()['revisions'], [])
            complete.return_value = json.dumps(OUTPUT)
            def change_source(*args, **kwargs):
                with Store(self.db) as store: store.correct_description(self.job.id, 'Changed posting.', 0, 'Fictional source')
                return json.dumps(OUTPUT)
            complete.side_effect = change_source
            self.assertEqual(self.call('POST', path, self.request()).status_code, 422)
            self.assertEqual(self.call('GET', path).json()['revisions'], [])

    def test_source_change_invalidates_reviewed_export(self):
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)):
            path = self.endpoint()
            rid = self.call('POST', path, self.request()).json()['revisions'][0]['id']
            self.call('POST', path + f'/{rid}/review', {'acknowledged': True})
            with Store(self.db) as store: store.correct_description(self.job.id, 'Changed posting.', 0, 'Fictional source')
            self.assertTrue(self.call('GET', path).json()['revisions'][0]['stale'])
            self.assertEqual(self.call('GET', path + f'/{rid}/resume.txt').status_code, 422)
