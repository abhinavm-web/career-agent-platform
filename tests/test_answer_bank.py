"""Offline, authenticated dashboard tests for candidate-entered application answers."""
import unittest

import test_dashboard
from career_agent.storage import Store


@unittest.skipUnless(test_dashboard.HAS_WEB, 'Install dashboard test extras')
class AnswerBankTests(unittest.TestCase):
    call = test_dashboard.DashboardTests.call
    bank = '/api/workspace/application-answers'

    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        self.role = f'/api/jobs/{self.job.id}/application-answers'
        self.profile_input = dict(topic='sponsorship_now', answer='Fictional answer.',
                                  confirmed=True, expected_revision=0)
        self.reuse = dict(self.profile_input, question='  Need sponsorship\nfor <this> role?  ',
                          mode='reuse', profile_revision=1)

    def save(self, path, data):
        result = self.call('PUT', path, data)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_empty_until_confirmed_and_separate_topics(self):
        entries = self.call('GET', self.bank).json()
        topics = {row['topic'] for row in entries}
        self.assertTrue({'work_authorization', 'sponsorship_now', 'sponsorship_future',
                         'h1b_transfer', 'compensation', 'availability'} <= topics)
        self.assertTrue(all(row['answer'] == '' for row in entries))
        for invalid in ({'confirmed': False}, {'confirmed': 'true'}, {'topic': []},
                        {'answer': None}, {'answer': '  '}, {'expected_revision': False}):
            self.assertEqual(self.call('PUT', self.bank, self.profile_input | invalid).status_code, 422)
        entries = self.save(self.bank, self.profile_input)
        self.assertEqual([row['topic'] for row in entries if row['answer']], ['sponsorship_now'])
        self.assertEqual(self.call('GET', self.role).json()['answers'], [])
        self.assertEqual(self.call('GET', self.bank, token='wrong').status_code, 403)
        self.assertEqual(self.call('GET', self.role.replace(self.job.id, '0' * 16)).status_code, 404)
        self.assertNotIn('Fictional answer.', self.call('GET', '/api/jobs').text)
        self.assertNotIn('Fictional answer.', self.call('GET', '/api/workspace').text)

    def test_exact_reuse_stale_edits_revocation_and_role_override(self):
        self.save(self.bank, self.profile_input)
        for invalid, status in (({'confirmed': False}, 422), ({'question': '   '}, 422),
                                ({'profile_revision': 0}, 409), ({'answer': 'Something else'}, 409),
                                ({'topic': 'sponsorship_future'}, 409)):
            self.assertEqual(self.call('PUT', self.role, self.reuse | invalid).status_code, status)
        first = self.save(self.role, self.reuse)['answers'][0]
        self.assertEqual(first['question'], self.reuse['question'])
        self.assertEqual(first['answer'], self.profile_input['answer'])
        self.assertTrue(first['reused'])
        self.assertEqual(self.call('PUT', self.role, self.reuse).status_code, 409)
        clear = self.profile_input | {'expected_revision': 1, 'answer': ''}
        self.save(self.bank, clear)
        self.assertEqual(self.call('PUT', self.bank, clear).status_code, 409)
        stale = self.call('GET', self.role).json()['answers'][0]
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['answer'], '')
        self.save(self.bank, self.profile_input | {'expected_revision': 2})
        self.assertEqual(self.call('PUT', self.role, self.reuse | {'expected_revision': 1}).status_code, 409)
        own = self.reuse | {'mode': 'role', 'expected_revision': 1, 'answer': 'Only for alpha.'}
        self.save(self.role, own)
        self.save(self.bank, self.profile_input | {'expected_revision': 3, 'answer': 'New profile answer.'})
        state = self.call('GET', self.role).json()
        self.assertEqual(state['answers'][0]['answer'], 'Only for alpha.')
        self.assertFalse(state['answers'][0]['reused'])
        self.assertEqual(next(x for x in state['bank'] if x['topic'] == own['topic'])['answer'],
                         'New profile answer.')

    def test_independent_roles_wordings_and_open_questions(self):
        other = test_dashboard.fake_job('https://example.invalid/beta')
        with Store(self.db) as store:
            store.add_jobs([other])
        other_path = self.role.replace(self.job.id, other.id)
        pending = self.reuse | {'mode': 'unanswered', 'answer': ''}
        self.save(self.role, pending)
        self.save(self.role, pending | {'question': 'Different wording?'})
        self.assertEqual(len(self.call('GET', self.role).json()['answers']), 2)
        self.assertEqual(self.call('GET', other_path).json()['answers'], [])
        self.save(other_path, self.reuse | {'topic': 'other', 'mode': 'role', 'answer': 'Beta only.'})
        self.assertEqual(self.call('GET', self.role).json()['answers'][0]['answer'], '')
        with Store(self.db) as store:
            with store.connection:
                store.connection.execute("INSERT INTO applications(job_id,stage) VALUES(?,'submitted')", (other.id,))
        self.assertTrue(self.call('GET', other_path).json()['locked'])
        self.assertEqual(self.call('PUT', other_path, pending | {'expected_revision': 1}).status_code, 409)

    def test_submitted_role_retains_reviewed_wording_after_profile_correction(self):
        self.save(self.bank, self.profile_input)
        self.save(self.role, self.reuse)
        with Store(self.db) as store, store.connection:
            store.connection.execute("INSERT INTO applications(job_id,stage) VALUES(?,'submitted')", (self.job.id,))
        self.save(self.bank, self.profile_input | {'expected_revision': 1, 'answer': 'Corrected.'})
        row = self.call('GET', self.role).json()['answers'][0]
        self.assertEqual(row['historical_answer'], self.reuse['answer'])
        self.assertTrue(row['stale'])
        self.assertEqual(row['answer'], '')
