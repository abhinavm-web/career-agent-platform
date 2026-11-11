"""Requirement extraction uses fictional text and never infers a candidate deficit."""
import unittest

from career_agent.fit_evidence import requirement_evidence


class FitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.facts = [{'id': 'fact-01', 'statement': 'Built a fictional Python tool.', 'keywords': ['Python']}]

    def test_required_preferred_and_unknown_evidence(self):
        result = requirement_evidence('Required qualifications:\n- Python experience\n- Rust experience\nPreferred skills:\n- SQL\nResponsibilities:\n- Python work', self.facts)
        self.assertEqual([r['category'] for r in result['requirements']], ['required', 'required', 'preferred'])
        self.assertEqual(result['requirements'][0]['quote'], '- Python experience')
        self.assertEqual(result['requirements'][0]['suggested_facts'][0]['id'], 'fact-01')
        self.assertEqual(result['requirements'][1]['suggested_facts'], [])
        self.assertIn('do not establish a qualification gap', result['notice'])

    def test_ambiguous_prose_and_unlabeled_bullets_not_inferred(self):
        text = 'Python is not required.\n- Python duties\nNice to have: Python\nOther:\n- Python responsibilities'
        rows = requirement_evidence(text, self.facts)['requirements']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['category'], 'preferred')
        self.assertEqual(rows[0]['quote'], 'Nice to have: Python')

    def test_limits_are_visible(self):
        result = requirement_evidence('Requirements:\n' + '\n'.join('- skill ' + str(i) for i in range(30)), [])
        self.assertEqual(len(result['requirements']), 25)
        self.assertTrue(result['truncated'])
        result = requirement_evidence('Requirements:\n- ' + 'x' * 1001, [])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['requirements'], [])
