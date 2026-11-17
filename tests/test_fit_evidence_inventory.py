"""Whole-posting inventory regressions use invented employers and candidate demands."""
import unittest

from career_agent.fit_evidence import requirement_evidence, requirement_inventory


class RequirementInventoryTests(unittest.TestCase):
    def test_seven_role_heading_variants_extract_qualifications(self):
        fixtures = [
            ('Platform lead', '**Requirements:**\n- Experience designing shared APIs.\n**Preferred:**\n- Workflow orchestration.'),
            ('Enablement lead', '**Qualifications/Requirements:**\n- Nine years of enablement.\n**Desired Characteristics:**\n- Learning community experience.'),
            ('Engineering director', '**Standard Job Requirements**\n- Deliver production systems.\n**Education, Experience & Soft Skills**\n- Eleven years of technology work.'),
            ('Solutions architect', '**What You’ll Bring**\n- Python engineering.\n- (Preferred) Agent orchestration.'),
            ('Research scientist', '**Basic Qualifications**\n- Peer-reviewed research.\n**Preferred Qualifications**\n- Model evaluation.'),
            ('Clinical R&D lead', '**What We Look For**\n- Fourteen years of program leadership.\n- Medical-software validation.'),
            ('Product director', '**Required Qualifications**\n- Seven years of product leadership.\n**Preferred Qualifications**\n- Portfolio strategy.'),
        ]
        for title, text in fixtures:
            with self.subTest(title=title):
                excerpts = requirement_evidence(text, [])
                self.assertEqual(len(excerpts['requirements']), 2)
                self.assertFalse(excerpts['truncated'])
                result = requirement_inventory(text)
                self.assertTrue(result['complete'])
                self.assertEqual(len(result['requirements']), 2)
                for row in result['requirements']:
                    self.assertIn(row['quote'], text)

    def test_legal_and_pay_prose_do_not_extend_preferred_section(self):
        text = ('Basic Qualifications:\n- Python engineering.\nPreferred Qualifications:\n- Model evaluation.\n\n'
                'Example Labs is an equal opportunity employer and does not discriminate on the basis of protected status.\n\n'
                'Los Angeles County applicants: Follow lawful instructions and work cooperatively with others.\n\n'
                'Our inclusive culture empowers all employees to do their best work.\n\n'
                'The base salary range for this position is listed below. Benefits include insurance and paid leave.\n\n'
                'USA, Example City - 100,000.00 - 130,000.00 USD annually')
        excerpts = requirement_evidence(text, [])
        self.assertEqual([r['quote'] for r in excerpts['requirements']], ['- Python engineering.', '- Model evaluation.'])
        inventory = requirement_inventory(text)
        self.assertEqual([r['quote'] for r in inventory['requirements']], ['- Python engineering.', '- Model evaluation.'])
        self.assertTrue(inventory['complete'])

    def test_full_inventory_includes_responsibilities_and_buried_conditions(self):
        text = ('This role will report onsite to the regional office. Remote work is not available.\n\n'
                'Responsibilities:\n- Build internal platforms.\n**Platform Standards**\n- Review API designs.\n'
                'Requirements:\n- Python\n  and SQL experience.\n'
                'About Us:\nExample Labs builds useful software.\n\n'
                'Legal authorization to work is required. We will not sponsor individuals for employment visas.\n'
                'Benefits:\n- Paid holidays\n\nCandidates must travel twice each quarter.')
        rows = requirement_inventory(text)['requirements']
        quotes = [r['quote'] for r in rows]
        self.assertIn('- Build internal platforms.', quotes)
        self.assertIn('- Review API designs.', quotes)
        self.assertIn('- Python\n  and SQL experience.', quotes)
        self.assertNotIn('- Paid holidays', quotes)
        conditions = [r for r in rows if r['category'] == 'condition']
        self.assertEqual(len(conditions), 3)
        for row in rows:
            self.assertIn(row['quote'], text)
        self.assertEqual([r['id'] for r in rows], ['r'+str(i+1) for i in range(len(rows))])

    def test_unheaded_and_unrecognized_sections_remain_accountable(self):
        text = ('You must have experience implementing production systems.\n\n'
                '**Unusual Expertise**\n- Novel platform knowledge.\n\n'
                '**Strong written communication**\n\n'
                'A degree is helpful but not required.')
        result = requirement_inventory(text)
        self.assertTrue(result['complete'])
        quotes = '\n'.join(r['quote'] for r in result['requirements'])
        self.assertIn('You must have experience implementing production systems.', quotes)
        self.assertIn('- Novel platform knowledge.', quotes)
        self.assertIn('**Strong written communication**', quotes)
        self.assertIn('A degree is helpful but not required.', quotes)

    def test_conditions_in_equal_opportunity_paragraph_not_discarded(self):
        text = ('Example Labs is an equal opportunity employer. '
                'We cannot provide employment visa sponsorship for this opening.')
        rows = requirement_inventory(text)['requirements']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['quote'], text)
        self.assertEqual(rows[0]['category'], 'condition')

    def test_salary_metadata_and_closing_mission_statement_are_not_requirements(self):
        text = ('**Overview**\n* Salary: 100,000 - 130,000 USD Annual\n'
                '**What We Look For**\n- Python engineering.\n\n'
                'At Example Labs, our purpose is to improve access to useful tools for everyone.')
        legacy = requirement_evidence(text, [])
        self.assertEqual([r['quote'] for r in legacy['requirements']], ['- Python engineering.'])
        quotes = [r['quote'] for r in requirement_inventory(text)['requirements']]
        self.assertFalse(any('Salary:' in q or 'our purpose' in q for q in quotes))

    def test_candidate_demand_inside_otherwise_excluded_sections_is_not_lost(self):
        text = ('Benefits:\n- Paid holidays\n- Applicants must hold an engineering license.\n'
                'About Us:\nMust possess advanced Python skills.\n\n'
                'The salary range is posted separately. Candidates must complete a technical assessment.')
        result = requirement_inventory(text)
        self.assertTrue(result['complete'])
        quotes = [r['quote'] for r in result['requirements']]
        self.assertEqual(len(quotes), 3)
        self.assertTrue(any('engineering license' in q for q in quotes))
        self.assertTrue(any('advanced Python' in q for q in quotes))
        self.assertTrue(any('technical assessment' in q for q in quotes))

    def test_overviews_are_retained_as_context_without_phantom_responsibilities(self):
        text = ('**Position Summary**\nExample Labs makes collaboration software.\n\n'
                '**About This Job**\nOur platform helps organizations communicate.\n\n'
                '**Role Summary/Purpose:**\nThe team works on useful products.\n\n'
                '**Responsibilities**\n- Lead the platform team.\n'
                '**Required Qualifications**\n- Python experience.')
        result = requirement_inventory(text)
        self.assertTrue(result['complete'])
        self.assertEqual([r['category'] for r in result['requirements']],
                         ['unspecified', 'unspecified', 'unspecified', 'responsibility', 'required'])
        self.assertEqual([r['quote'] for r in result['requirements'][:3]],
                         ['Example Labs makes collaboration software.',
                          'Our platform helps organizations communicate.',
                          'The team works on useful products.'])

    def test_no_twenty_five_item_cutoff_and_explicit_upper_bounds(self):
        text = 'Requirements:\n'+'\n'.join('- Skill '+str(i) for i in range(60))
        result = requirement_inventory(text)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['requirements']), 60)
        oversized = requirement_inventory('Requirements:\n'+'\n'.join('- Skill '+str(i) for i in range(301)))
        self.assertFalse(oversized['complete'])
        self.assertEqual(len(oversized['requirements']), 300)
        self.assertIn('300', oversized['notices'][0])
        huge = requirement_inventory('x'*100001)
        self.assertFalse(huge['complete'])
        self.assertEqual(huge['requirements'], [])

    def test_exact_spans_preserve_crlf_and_internal_trailing_whitespace(self):
        text = '**Requirements**\r\n- Python experience,  \r\n  but no degree is required.\r\n'
        row = requirement_inventory(text)['requirements'][0]
        self.assertIn(row['quote'], text)
        self.assertEqual(row['quote'], '- Python experience,  \r\n  but no degree is required.')
        self.assertEqual(row['category'], 'unspecified')

    def test_empty_or_boilerplate_only_never_claims_complete(self):
        for text in ('', 'Requirements:', 'Benefits:\n- Paid holidays', 'What We Look For:'):
            with self.subTest(text=text):
                self.assertFalse(requirement_inventory(text)['complete'])
                self.assertTrue(requirement_evidence(text, [])['truncated'])

    def test_negation_and_optional_qualifiers_are_preserved(self):
        text = 'Requirements:\n- Python is optional.\n- No degree is required.\n- SQL experience.\nPreferred:\n- Rust is not preferred.'
        rows = requirement_inventory(text)['requirements']
        self.assertEqual([r['category'] for r in rows], ['unspecified','unspecified','required','unspecified'])


if __name__ == '__main__':
    unittest.main()
