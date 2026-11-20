"""Grounding and completeness checks for comparisons against an uploaded resume."""
import json
import unittest

from career_agent.resume_gap import compare


class ResumeGapTests(unittest.TestCase):
    resume = 'Built a Python reporting service.'

    def answers(self, prompt):
        items = json.loads(prompt)['posting_items']
        return json.dumps({'items': [
            {'id': source['id'], 'status': 'partial', 'resume_quote': self.resume,
             'difference': 'The resume describes development but gives no service scale.',
             'next_step': 'Add the measured document volume if recorded.'}
            for source in reversed(items)]})

    def test_all_batches_preserve_source_order_and_citations(self):
        posting = 'Required qualifications\n' + '\n'.join(
            f'- Build reporting services for workflow {i}.' for i in range(9))
        rows = compare(posting, self.resume, self.answers)
        self.assertEqual(len(rows), 9)
        for i, row in enumerate(rows):
            self.assertIn(f'workflow {i}', row['quote'])
            self.assertEqual(row['resume_quote'], self.resume)

    def test_missing_item_and_invented_citation_fail_comparison(self):
        for response in [lambda _: '{"items":[]}',
                         lambda prompt: self.answers(prompt).replace('Python', 'Rust')]:
            with self.assertRaisesRegex(ValueError, 'invalid resume citation'):
                compare('Requirements\n- Build reporting services.', self.resume, response)
