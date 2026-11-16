import copy
import json
import unittest
from unittest.mock import patch

from career_agent import ai
from career_agent.profile_review import generate
from career_agent.storage import Store
from career_agent.workspace import CandidateWorkspace
import test_dashboard
from test_document_revisions import ROUTE

QUESTIONS = {'questions': [
    {'question': 'Does the result describe cycle time or team efficiency, and what was measured?',
     'fact_ids': ['experience-01'], 'excerpt_id': '1'},
    {'question': 'Was the tool used in a prototype, evaluation or production implementation?',
     'fact_ids': [], 'excerpt_id': '2'}]}
RESUME = 'Reduced elapsed QA cycle time by 25%.\nUsed FictionalSearch.'


class ProfileReviewTests(unittest.TestCase):
    call = test_dashboard.DashboardTests.call

    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.call('POST', '/api/workspace/import', {'acknowledged': True})
        self.w = self.call('GET', '/api/workspace').json()['workspace']
        self.request = {'route': 'claude', 'route_fingerprint': ai.route_fingerprint('claude', ROUTE),
                        'resume_text': RESUME, 'acknowledged': True}

    def create(self, output=None):
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', return_value=json.dumps(output or QUESTIONS)) as complete:
            response = self.call('POST', '/api/workspace/reviews', self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('Private fictional evidence', complete.call_args.args[1])
        self.assertNotIn('Fictional Candidate', complete.call_args.args[1])
        self.assertRegex(json.loads(complete.call_args.args[1])['as_of_date_utc'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertIn('not a planned future departure', complete.call_args.kwargs['system'])
        return response.json()['reviews'][0]

    def answer(self, review, **changes):
        data = {'expected_revision': self.w['revision'], 'answer': 'Reduced elapsed QA cycle time by 25%.',
                'section': 'experience', 'replace_fact_id': 'experience-01', 'acknowledged': True}
        return self.call('POST', f"/api/workspace/reviews/{review['id']}/questions/0/answer", {**data, **changes})

    def test_review_respects_request_history_and_daily_limit(self):
        route = {**ROUTE, 'record_history': True, 'daily_request_limit': 1}
        request = {**self.request, 'route_fingerprint': ai.route_fingerprint('claude', route)}
        with patch('career_agent.ai.routes', return_value={'claude': route}), patch('career_agent.ai.complete', return_value=json.dumps(QUESTIONS)) as complete:
            first = self.call('POST', '/api/workspace/reviews', request)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertNotEqual(self.call('POST', '/api/workspace/reviews', request).status_code, 200)
            self.assertEqual(complete.call_count, 1)

    def test_questions_persist_without_changing_facts_and_require_consent(self):
        with patch('career_agent.ai.complete') as complete:
            self.assertEqual(self.call('POST', '/api/workspace/reviews', {**self.request, 'acknowledged': False}).status_code, 422)
            complete.assert_not_called()
        review = self.create()
        self.assertEqual(self.call('GET', '/api/workspace').json()['workspace'], self.w)
        self.assertEqual(self.call('GET', '/api/workspace/reviews').json()['reviews'][0], review)
        self.assertEqual(review['questions'][0]['quote'], RESUME.splitlines()[0])
        self.assertEqual(self.call('GET', '/api/workspace/reviews', token='wrong').status_code, 403)

    def test_replace_is_atomic_unverified_and_retry_cannot_duplicate(self):
        review = self.create()
        saved = self.answer(review)
        self.assertEqual(saved.status_code, 200, saved.text)
        current = self.call('GET', '/api/workspace').json()['workspace']
        self.assertEqual(len(current['facts']), 1)
        self.assertFalse(current['facts'][0]['verified_by_candidate'])
        self.assertEqual(current['facts'][0]['source_note'], self.w['facts'][0]['source_note'])
        self.assertEqual(current['profile'], self.w['profile'])
        self.assertEqual(self.answer(review, expected_revision=current['revision']).status_code, 409)
        self.call('POST', '/api/workspace/facts/experience-01/verification',
                  {'expected_revision': current['revision'], 'verified': True, 'acknowledged': True})
        self.assertTrue(self.call('GET', '/api/workspace/reviews').json()['reviews'][0]['questions'][0]['verified'])

    def test_new_fact_duplicate_and_invalid_replacement_roll_back(self):
        review = self.create()
        for changes in ({'replace_fact_id': 'unknown'}, {'acknowledged': False},
                        {'replace_fact_id': '', 'answer': self.w['facts'][0]['statement']}, {'answer': ''}):
            self.assertEqual(self.answer(review, **changes).status_code, 422)
        self.assertEqual(self.call('GET', '/api/workspace').json()['workspace'], self.w)
        self.assertEqual(self.answer(review, replace_fact_id='').status_code, 200)
        current = self.call('GET', '/api/workspace').json()['workspace']
        self.assertEqual(current['facts'][0], self.w['facts'][0])
        self.assertEqual(len(current['facts']), 2)
        self.assertFalse(current['facts'][1]['verified_by_candidate'])

    def test_changed_sources_and_stale_revision_block_answer(self):
        review = self.create()
        self.w['facts'][0]['statement'] = 'Corrected fictional source.'
        updated = self.call('PUT', '/api/workspace', {'expected_revision': self.w['revision'], 'profile': self.w['profile'], 'facts': self.w['facts']}).json()['workspace']
        self.assertEqual(self.answer(review).status_code, 409)
        self.assertEqual(self.answer(review, expected_revision=updated['revision']).status_code, 409)
        self.assertTrue(self.call('GET', '/api/workspace/reviews').json()['reviews'][0]['questions'][0]['stale'])

    def test_defer_does_not_invalidate_workspace_or_verify_anything(self):
        review = self.create(); path = f"/api/workspace/reviews/{review['id']}/questions/0/defer"
        self.assertEqual(self.call('POST', path, {'acknowledged': False}).status_code, 422)
        result = self.call('POST', path, {'acknowledged': True})
        self.assertEqual(result.json()['reviews'][0]['questions'][0]['status'], 'unresolved')
        self.assertEqual(self.call('GET', '/api/workspace').json()['workspace'], self.w)
        self.assertEqual(self.call('POST', path, {'acknowledged': True}).status_code, 409)

    def test_model_cannot_invent_sources_or_return_extra_mutation_fields(self):
        bad = []
        for change in ({'fact_ids': ['invented']}, {'excerpt_id': '999'}, {'fact_ids': [], 'excerpt_id': ''}, {'verified': True}, {'question': ''}):
            output = copy.deepcopy(QUESTIONS); output['questions'][0].update(change); bad.append(output)
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}):
            for output in bad:
                with patch('career_agent.ai.complete', return_value=json.dumps(output)), self.assertRaises(ValueError):
                    generate('claude', self.w, RESUME)

    def test_profile_change_while_model_runs_discards_questions(self):
        def complete(*args, **kwargs):
            with Store(self.db) as store:
                CandidateWorkspace(store).verify(self.w['revision'], 'experience-01', False, True)
            return json.dumps(QUESTIONS)
        with patch('career_agent.ai.routes', return_value={'claude': ROUTE}), patch('career_agent.ai.complete', side_effect=complete):
            self.assertEqual(self.call('POST', '/api/workspace/reviews', self.request).status_code, 409)
        self.assertEqual(self.call('GET', '/api/workspace/reviews').json()['reviews'], [])

    def test_invalid_answer_leaves_question_open(self):
        review = self.create()
        self.assertEqual(self.answer(review, section='invented').status_code, 422)
        q = self.call('GET', '/api/workspace/reviews').json()['reviews'][0]['questions'][0]
        self.assertEqual(q['status'], 'open')
        self.assertEqual(self.call('GET', '/api/workspace').json()['workspace'], self.w)
