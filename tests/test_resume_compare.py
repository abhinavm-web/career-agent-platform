"""Offline tests for the original-résumé baseline, role comparison and inline AI question."""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.models import CandidateProfile, Job
from career_agent.storage import Store

HAS_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None

ORIGINAL = "Fictional Candidate\nDelivered a fictional AI program involving Python and AWS.\nManaged a fictional budget."


@unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
class ResumeCompareTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / 'career.sqlite3'
        profile = CandidateProfile(target_titles=('AI Program Lead',), skills=('Python', 'AWS'),
                                   preferred_locations=(), allow_remote=True)
        (root / 'preferences.json').write_text(json.dumps(asdict(profile)))
        (root / 'facts.json').write_text(json.dumps({'name': 'Fictional Candidate', 'headline': 'AI', 'facts': [
            {'id': 'experience-01', 'section': 'experience', 'verified_by_candidate': True, 'source_note': 'Private fictional evidence.',
             'statement': 'Delivered a fictional AI program involving Python and AWS.', 'keywords': ['Python', 'AWS']},
            {'id': 'projects-01', 'section': 'projects', 'verified_by_candidate': True, 'source_note': 'Private fictional evidence.',
             'statement': 'Built a fictional Kubernetes platform for batch jobs.', 'keywords': ['Kubernetes']},
            {'id': 'skills-01', 'section': 'skills', 'verified_by_candidate': True, 'source_note': 'Private fictional evidence.',
             'statement': 'Cooked a fictional lasagna.', 'keywords': ['Cooking']}]}))
        self.job = Job.from_mapping({'company': 'Example', 'title': 'AI Program Lead', 'location': 'Remote USA',
            'job_url': 'https://example.invalid/jobs/alpha', 'is_remote': True,
            'description': 'About the role\nRequirements\n- Python and AWS delivery\n- Kubernetes operations\n- Executive stakeholder reporting\n'})
        with Store(self.db) as store:
            store.add_jobs([self.job])
        self.client = TestClient(create_app(DashboardConfig(db_path=self.db, packet_dir=root / 'packets',
            profile_path=root / 'preferences.json', facts_path=root / 'facts.json')), base_url='http://localhost')
        self.session = re.search(r'data-session="([^"]+)"', self.client.get('/').text).group(1)

    def call(self, method, path, payload=None):
        headers = {'X-Session-Token': self.session}
        return self.client.request(method, path, headers=headers, **({'json': payload} if payload is not None else {}))

    def save(self, text=ORIGINAL, revision=0, **extra):
        return self.call('PUT', '/api/workspace/resume-original',
                         {'text': text, 'filename': 'resume.txt', 'expected_revision': revision, 'acknowledged': True, **extra})

    def test_original_resume_is_saved_with_revision_checks(self):
        self.assertEqual(self.call('GET', '/api/workspace/resume-original').json()['resume']['revision'], 0)
        self.assertEqual(self.call('PUT', '/api/workspace/resume-original',
                                   {'text': ORIGINAL, 'filename': 'r.txt', 'expected_revision': 0}).status_code, 422)
        saved = self.save()
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['resume']['revision'], 1)
        self.assertEqual(self.save(revision=0).status_code, 409)
        self.assertEqual(self.save('   ', revision=1).status_code, 422)
        self.assertEqual(self.save('x' * 20001, revision=1).status_code, 422)
        again = self.save('Replacement fictional résumé.', revision=1)
        self.assertEqual(again.json()['resume']['revision'], 2)
        self.assertEqual(self.call('GET', '/api/workspace/resume-original').json()['resume']['text'], 'Replacement fictional résumé.')

    def test_comparison_marks_original_added_and_unaddressed_requirements(self):
        self.save()
        view = self.call('GET', f'/api/jobs/{self.job.id}/resume-compare').json()
        bullets = {b['fact_id']: b for section in view['sections'] for b in section['bullets']}
        self.assertTrue(bullets['experience-01']['in_original'])
        self.assertEqual(bullets['experience-01']['matched_terms'], ['Python', 'AWS'])
        self.assertFalse(bullets['projects-01']['in_original'])      # added for this posting
        self.assertNotIn('skills-01', bullets)                       # irrelevant and not in the original
        self.assertEqual(view['unused_fact_count'], 1)
        self.assertEqual([gap['quote'] for gap in view['gaps']], ['- Executive stakeholder reporting'])
        self.assertEqual(view['original']['text'], ORIGINAL)

    def test_staged_unverified_answer_is_not_recommended_until_verified(self):
        imported = self.call('POST', '/api/workspace/import', {'acknowledged': True}).json()['workspace']
        staged = self.call('POST', f'/api/jobs/{self.job.id}/experience-answer', {
            'expected_revision': imported['revision'], 'job_quote': '- Executive stakeholder reporting',
            'answer': 'Delivered fictional stakeholder reporting to executives monthly.', 'section': 'experience', 'acknowledged': True}).json()
        view = self.call('GET', f'/api/jobs/{self.job.id}/resume-compare').json()
        self.assertNotIn(staged['fact_id'], [b['fact_id'] for s in view['sections'] for b in s['bullets']])
        self.assertEqual(len(view['gaps']), 1)
        self.assertNotIn('Private fictional evidence', json.dumps(view))
        verified = self.call('POST', f'/api/workspace/facts/{staged["fact_id"]}/verification', {
            'expected_revision': staged['workspace']['revision'], 'verified': True, 'acknowledged': True})
        self.assertEqual(verified.status_code, 200)
        after = self.call('GET', f'/api/jobs/{self.job.id}/resume-compare').json()
        added = {b['fact_id']: b for s in after['sections'] for b in s['bullets']}[staged['fact_id']]
        self.assertFalse(added['in_original'])
        self.assertEqual(added['answers'], '- Executive stakeholder reporting')
        self.assertEqual(after['gaps'], [])

    def test_comparison_works_without_an_original_resume(self):
        view = self.call('GET', f'/api/jobs/{self.job.id}/resume-compare').json()
        self.assertEqual(view['original']['revision'], 0)
        self.assertTrue(all(not b['in_original'] for s in view['sections'] for b in s['bullets']))

    def test_ai_question_is_consented_scoped_and_read_only(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        url = f'/api/jobs/{self.job.id}/ai-experience-question'
        fingerprint = ai.route_fingerprint('local', route['local'])
        request = {'route': 'local', 'route_fingerprint': fingerprint, 'requirement': '- Executive stakeholder reporting',
                   'draft_answer': '', 'acknowledged': True}
        before = self.call('GET', '/api/workspace').json()
        with patch('career_agent.ai.routes', return_value=route), \
                patch('career_agent.ai.complete', return_value='Where did you report to executives, and how often?') as complete:
            self.assertEqual(self.call('POST', url, {**request, 'acknowledged': False}).status_code, 422)
            complete.assert_not_called()
            self.assertEqual(self.call('POST', url, {**request, 'requirement': 'Invented requirement'}).status_code, 422)
            complete.assert_not_called()
            response = self.call('POST', url, request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['question'], 'Where did you report to executives, and how often?')
            prompt = json.loads(complete.call_args.args[1])
            self.assertEqual(set(prompt['verified_facts']), {'experience-01', 'projects-01', 'skills-01'})
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)


if __name__ == '__main__':
    unittest.main()
