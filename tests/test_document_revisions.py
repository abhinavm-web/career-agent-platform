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

    def validate_revision(self, path):
        result = {'status': 'completed', 'complete': True, 'export_allowed': True,
                  'support_audit': {'complete': True, 'checked': 0},
                  'requirements': [], 'claims': {'complete': True, 'passages': []}, 'notices': []}
        with patch('career_agent.resume_validation.validate', return_value=result):
            response = self.call('POST', path + '/validate', {k:v for k,v in self.request().items() if k in {'route','route_fingerprint','acknowledged'}})
        self.assertEqual(response.status_code, 200, response.text)

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
            self.validate_revision(path + f'/{rid}')
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
            self.validate_revision(path + f'/{rid}')
            self.call('POST', path + f'/{rid}/review', {'acknowledged': True})
            with Store(self.db) as store: store.correct_description(self.job.id, 'Changed posting.', 0, 'Fictional source')
            self.assertTrue(self.call('GET', path).json()['revisions'][0]['stale'])
            self.assertEqual(self.call('GET', path + f'/{rid}/resume.txt').status_code, 422)

    def test_new_resume_omits_previous_draft_and_records_reviewed_coverage(self):
        description = 'Requirements:\n- Python reporting\n- SQL\n- AWS\n- Leadership\n- Education'
        with Store(self.db) as store: store.correct_description(self.job.id, description, 0, 'Fictional source')
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)) as complete:
            path = self.endpoint()
            first = self.call('POST', path, self.request()).json()['revisions'][0]['id']
            created = self.call('POST', path, {**self.request(first), 'fresh': True})
            self.assertEqual(created.status_code, 200, created.text)
            revision = created.json()['revisions'][0]; rid = revision['id']
            prompt = json.loads(complete.call_args.args[1])
            self.assertIsNone(prompt['previous_unverified_draft'])
            self.assertTrue(revision['fresh'])
            self.assertEqual(len(revision['requirement_quotes']), 5)
            self.validate_revision(path + f'/{rid}')
            self.assertEqual(self.call('POST', path + f'/{rid}/review', {'acknowledged': True, 'coverage': [True]*5}).status_code, 422)
            self.assertEqual(self.call('POST', path + f'/{rid}/review', {'acknowledged': True, 'coverage': [99]*5}).status_code, 422)
            result = self.call('POST', path + f'/{rid}/review', {'acknowledged': True, 'coverage': [0,0,0,0,None]})
            self.assertEqual(result.status_code, 200, result.text)
            coverage = result.json()['revisions'][0]['coverage']
            self.assertEqual(coverage['percent'], 80)
            self.assertFalse(coverage['target_met'])
            self.assertEqual(self.call('GET', path).json()['revisions'][0]['coverage']['selections'], [0,0,0,0,None])
            self.assertIn('Experience', self.call('GET', path + f'/{rid}/resume.txt').text)

    def test_new_experience_requires_separate_candidate_verification(self):
        workspace = self.call('POST', '/api/workspace/import', {'acknowledged': True}).json()['workspace']
        path = '/api/jobs/' + self.job.id + '/experience-answer'
        answer = {'expected_revision': workspace['revision'], 'job_quote': 'Seeking Python and AWS.',
                  'answer': 'Led a documented Python training workshop.', 'section': 'experience', 'acknowledged': True}
        self.assertEqual(self.call('POST', path, {**answer, 'job_quote': 'Not in posting'}).status_code, 409)
        saved = self.call('POST', path, answer)
        self.assertEqual(saved.status_code, 200, saved.text)
        value = saved.json(); fact = value['workspace']['facts'][-1]
        self.assertFalse(fact['verified_by_candidate'])
        self.assertEqual(fact['statement'], answer['answer'])
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)) as complete:
            created = self.call('POST', self.endpoint(), self.request())
            self.assertEqual(created.status_code, 200, created.text)
            self.assertNotIn(fact['id'], json.loads(complete.call_args.args[1])['verified_facts'])
            verified = self.call('POST', '/api/workspace/facts/' + fact['id'] + '/verification', {
                'expected_revision': value['workspace']['revision'], 'verified': True, 'acknowledged': True})
            self.assertEqual(verified.status_code, 200, verified.text)
            parent = created.json()['revisions'][0]['id']
            created = self.call('POST', self.endpoint(), {**self.request(parent), 'fresh': True})
            self.assertEqual(created.status_code, 200, created.text)
            self.assertIn(fact['id'], json.loads(complete.call_args.args[1])['verified_facts'])

    def test_incomplete_requirement_parse_cannot_claim_coverage_target_met(self):
        description = 'Required skills:\n- Python\n**Specialized Role Requirements**\n- Novel expertise'
        with Store(self.db) as store: store.correct_description(self.job.id, description, 0, 'Fictional source')
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(OUTPUT)):
            path = self.endpoint()
            revision = self.call('POST', path, self.request()).json()['revisions'][0]
            self.assertTrue(revision['requirements_truncated'])
            self.validate_revision(path + '/' + str(revision['id']))
            result = self.call('POST', path + '/' + str(revision['id']) + '/review', {'acknowledged': True, 'coverage': [0]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(result.json()['revisions'][0]['coverage']['target_met'])
