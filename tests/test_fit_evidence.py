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

    def test_indented_continuations_preserve_qualifiers_and_bullet_boundaries(self):
        text = ('Requirements:\n- Python experience is helpful,\n'
                '  but not required.\n- Rust experience\n'
                '  in production.\nPreferred skills:\n- SQL')
        rows = requirement_evidence(text, self.facts)['requirements']
        self.assertEqual([row['quote'] for row in rows],
                         ['- Python experience is helpful,\n  but not required.',
                          '- Rust experience\n  in production.', '- SQL'])
        self.assertEqual([row['category'] for row in rows], ['required', 'required', 'preferred'])
        self.assertEqual(rows[0]['suggested_facts'][0]['id'], 'fact-01')

    def test_long_wrapped_bullet_is_omitted_whole(self):
        result = requirement_evidence('Requirements:\n- Python\n  ' + 'x' * 1000, self.facts)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['requirements'], [])

    def test_indented_next_bullet_is_not_swallowed_by_previous_quote(self):
        result = requirement_evidence('Requirements:\n- Python\n  - Rust\n  ## Responsibilities\n  - SQL', self.facts)
        self.assertEqual([row['quote'] for row in result['requirements']], ['- Python', '- Rust'])
        text = 'Requirements:\n- Python\n  **Preferred**\n  - SQL'
        rows = requirement_evidence(text, self.facts)['requirements']
        self.assertEqual([(row['category'], row['quote']) for row in rows],
                         [('required', '- Python'), ('preferred', '- SQL')])

    def test_markdown_headings_preserve_source_and_do_not_infer_requirement_level(self):
        text = ('**Required Qualifications:**\n- Python experience\n'
                '### Qualifications\n* Rust experience\n'
                '**Basic Qualifications**\n1. Python delivery\n'
                '**Preferred Skills:** SQL\n'
                '## Responsibilities\n- Python duties')
        rows = requirement_evidence(text, self.facts)['requirements']
        self.assertEqual([row['category'] for row in rows], ['required', 'unspecified', 'unspecified', 'preferred'])
        self.assertEqual([row['source_heading'] for row in rows],
                         ['**Required Qualifications:**', '### Qualifications', '**Basic Qualifications**', '**Preferred Skills:** SQL'])
        self.assertEqual([row['quote'] for row in rows],
                         ['- Python experience', '* Rust experience', '1. Python delivery', '**Preferred Skills:** SQL'])
        self.assertEqual(rows[1]['suggested_facts'], [])
        self.assertEqual(rows[2]['suggested_facts'][0]['id'], 'fact-01')

    def test_markup_does_not_turn_ambiguous_prose_into_a_heading(self):
        for heading in ('**Qualifications** may vary', '### Qualifications for contractors',
                        '**Requirements vary**', 'Qualifications may be waived'):
            with self.subTest(heading=heading):
                self.assertEqual(requirement_evidence(heading + '\n- Python work', self.facts)['requirements'], [])

    def test_candidate_sections_and_preferred_heading_keep_explicit_boundaries(self):
        text = ('**About You**\n* Python experience\n'
                '**What You’ll Bring**\n- Python communication\n'
                '**Preferred** :\n* SQL experience\n'
                '**About Us**\n- Python services')
        rows = requirement_evidence(text, self.facts)['requirements']
        self.assertEqual([row['category'] for row in rows], ['candidate_context', 'candidate_context', 'preferred'])
        self.assertEqual([row['source_heading'] for row in rows], ['**About You**', '**What You’ll Bring**', '**Preferred** :'])
        self.assertEqual([row['quote'] for row in rows], ['* Python experience', '- Python communication', '* SQL experience'])
        self.assertEqual(rows[0]['suggested_facts'][0]['id'], 'fact-01')
        self.assertEqual(rows[2]['suggested_facts'], [])

    def test_about_you_prose_does_not_become_a_candidate_section(self):
        for text in ('Learn about you and your career.\n- Python tasks',
                     '**About your employer**\n- Python services',
                     'What you’ll bring to our customers\n- Python services'):
            with self.subTest(text=text):
                self.assertEqual(requirement_evidence(text, self.facts)['requirements'], [])
