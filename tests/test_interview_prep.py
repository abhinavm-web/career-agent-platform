"""Interview prompts are bounded, local and do not fabricate employer questions."""
import unittest
from career_agent.interview_prep import preparation_guide


class InterviewPreparationTests(unittest.TestCase):
    def test_empty_posting_has_general_questions_without_fabricated_excerpts(self):
        guide = preparation_guide({'job_id': 'a', 'description_revision': 0, 'workspace_revision': 0,
                                   'requirements': [], 'truncated': False})
        self.assertEqual(guide['prompts'], [])
        self.assertEqual(len(guide['general_questions']), 3)
        self.assertIn('not actual employer interview questions', guide['notice'])
        self.assertFalse(guide['truncated'])

    def test_guide_caps_excerpts_and_explicitly_reports_omitted_fact_matches(self):
        facts = [{'id': str(i), 'statement': 'Fictional fact', 'matched_keywords': ['Python']} for i in range(3)]
        row = {'category': 'unspecified', 'source_heading': 'Qualifications',
               'quote': '- Python', 'suggested_facts': facts}
        guide = preparation_guide({'job_id': 'a', 'description_revision': 1, 'workspace_revision': 2,
                                   'requirements': [row] * 9, 'truncated': False})
        self.assertEqual(len(guide['prompts']), 8)
        self.assertEqual(guide['prompts'][0]['suggested_facts'], facts[:2])
        self.assertEqual(guide['prompts'][0]['additional_fact_matches'], 1)
        self.assertEqual(guide['prompts'][0]['quote'], '- Python')
        self.assertTrue(guide['truncated'])
