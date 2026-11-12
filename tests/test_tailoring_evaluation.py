"""Offline fictional semantic review fixture; warnings never establish correctness."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from career_agent import ai
from career_agent.candidate import FactBank
from career_agent.models import Job
from career_agent.tailoring_checks import wording_review_warnings
from scripts.evaluate_tailoring import evaluate
from scripts.evaluate_live_tailoring import evaluate as evaluate_live
from career_agent.ai_history import DailyRequestLimit

ROOT = Path(__file__).resolve().parents[1]


class TailoringEvaluationTests(unittest.TestCase):
    def test_live_evaluation_requires_consent_and_only_sends_fictional_cases(self):
        route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        with patch('scripts.evaluate_live_tailoring.ai.routes', return_value={'local': route}), \
             patch('scripts.evaluate_live_tailoring.run_recorded') as run:
            with self.assertRaisesRegex(ValueError, 'Confirm'):
                evaluate_live('local', Path('fictional.sqlite3'))
            run.assert_not_called()
            run.return_value = {'model': 'fictional', 'reported_model': 'resolved', 'suggestions': []}
            report = evaluate_live('local', Path('fictional.sqlite3'), True)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(len(report['cases']), 3)
            self.assertTrue(all(row['status'] == 'validated_response' for row in report['cases']))
            self.assertIn('not a benchmark', report['notice'])
            self.assertTrue(all(call.args[4].company == 'Fictional employer' for call in run.call_args_list))
            self.assertTrue(all(call.kwargs['expected_route_fingerprint'] for call in run.call_args_list))

    def test_live_evaluation_stops_after_limit_or_route_change(self):
        route = {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        with patch('scripts.evaluate_live_tailoring.ai.routes', return_value={'local': route}), \
             patch('scripts.evaluate_live_tailoring.run_recorded', side_effect=DailyRequestLimit('Local limit')) as run:
            report = evaluate_live('local', Path('fictional.sqlite3'), True)
            self.assertEqual(report['cases'][0]['status'], 'not_sent_limit')
            self.assertEqual(run.call_count, 1)
        with patch('scripts.evaluate_live_tailoring.ai.routes', return_value={'local': route}), \
             patch('scripts.evaluate_live_tailoring.run_recorded', side_effect=ValueError('Model destination or configuration changed')) as run:
            report = evaluate_live('local', Path('fictional.sqlite3'), True)
            self.assertEqual(report['cases'][0]['status'], 'failed')
            self.assertEqual(run.call_count, 1)
    def test_fixed_benchmark_reports_flagged_and_missed_cases(self):
        fixture = json.loads((ROOT / 'examples' / 'tailoring-evaluation.json').read_text(encoding='utf-8'))
        report = evaluate(fixture)
        self.assertEqual((report['total'], report['unsupported_cases'], report['unsupported_with_warning'],
                          report['unsupported_without_warning'], report['supported_cases'], report['supported_with_warning']),
                         (18, 12, 8, 4, 6, 1))
        by_id = {row['id']: row for row in report['cases']}
        self.assertFalse(by_id['new-skill-blindspot']['warning_emitted'])
        self.assertEqual(by_id['new-skill-blindspot']['reference_label'], 'unsupported')
        self.assertTrue(by_id['false-positive-format']['warning_emitted'])
        self.assertIn('not semantic validation', report['scope'])

    def test_invalid_fixture_rejected(self):
        for data in ({}, {'cases': []}, {'cases': [{'id': 'x'}]}):
            with self.subTest(data=data), self.assertRaises(ValueError): evaluate(data)

    def test_warning_categories_and_no_clean_bill_of_health(self):
        self.assertTrue(wording_review_warnings('Built 3 dashboards.', 'Built 30 dashboards.'))
        self.assertTrue(wording_review_warnings('Change was 5%.', 'Change was -5%.'))
        self.assertTrue(wording_review_warnings('Did not lead a team.', 'Led a team.'))
        self.assertTrue(wording_review_warnings('Helped deliver a project.', 'Led a project.'))
        self.assertEqual(wording_review_warnings('Built a Python tool.', 'Built a Rust tool.'), [])

    def test_model_output_with_valid_citations_still_shows_warning_and_stays_unverified(self):
        job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Data Analyst',
            'job_url': 'https://example.invalid/role', 'description': 'Python skills.'})
        bank = FactBank.parse({'name': 'Fictional candidate', 'facts': [{'id': 'fact-01',
            'section': 'experience', 'statement': 'Built a Python dashboard.', 'keywords': ['Python'],
            'source_note': 'Private fictional note', 'verified_by_candidate': True}]})
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        output = {'suggestions': [{'fact_id': 'fact-01', 'job_quote': 'Python skills.',
                                   'wording': 'Built a Python dashboard and cut costs by 40%.'}]}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)):
            result = ai.tailoring_suggestions(job, bank, 'local', True)
        self.assertTrue(result['suggestions'][0]['review_warnings'])
        self.assertIn('absence of warnings do not prove', result['notice'])
        self.assertNotIn('Private fictional note', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
