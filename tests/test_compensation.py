"""Explicit salary comparisons are conservative and do not change ranking."""
import unittest
from dataclasses import replace

from career_agent.models import CandidateProfile, Job
from career_agent.scoring import constraint_evidence, score_job


class CompensationTests(unittest.TestCase):
    def setUp(self):
        self.job = Job(company='Fictional', title='Engineer', location='', url='https://example.invalid/role')
        self.profile = {'minimum_annual_base_usd': 150_000}

    def review(self, description):
        return constraint_evidence(replace(self.job, description=description), self.profile)[0]

    def test_explicit_annual_usd_range_quotes_source_and_flags_only_below_minimum(self):
        row = self.review('Base salary: USD $100,000 - $140,000 per year')
        self.assertEqual(row['status'], 'flagged')
        self.assertIn('Base salary: USD $100,000 - $140,000 per year', row['detail'])
        self.assertIn('maximum is below your $150,000 minimum', row['detail'])
        row = self.review('Annual base salary: US$130k – $160k annually')
        self.assertEqual(row['status'], 'source_marked')
        self.assertIn('actual offers and eligibility are not confirmed', row['detail'])
        self.assertEqual(self.review('Base salary: $150000 per year USD')['status'], 'source_marked')

    def test_ambiguous_pay_remains_unknown(self):
        descriptions = [
            '', 'Salary: USD $200,000 per year', 'Base salary: $200,000 per year',
            'Base salary: USD $100 - $150 per hour',
            'Base salary: CAD $100,000 - $200,000 per year',
            'Base salary: USD $100,000 - $200,000 per year plus bonus',
            'Base salary: USD $100,000 - $200,000 per year\nSigning bonus: $10,000',
            'Base salary: USD $100,000 - $200,000 per year\nBase salary: USD $200,000 - $300,000 per year',
            'Base salary: USD $200,000 - $100,000 per year',
            'Base salary: USD $' + '9' * 20000 + ' per year',
        ]
        for description in descriptions:
            with self.subTest(description=description[:80]):
                self.assertEqual(self.review(description)['status'], 'unknown')

    def test_absent_preference_and_unchanged_soft_score(self):
        self.assertEqual(constraint_evidence(self.job, {}), [])
        profile = CandidateProfile(('Engineer',), (), (), True)
        scored = score_job(self.job, profile)
        self.review('Base salary: USD $100,000 per year')
        self.assertEqual(score_job(self.job, profile), scored)
