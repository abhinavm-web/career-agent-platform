"""Explicit work-arrangement labels are evidence, not eligibility decisions."""
import unittest
from dataclasses import replace

from career_agent.models import CandidateProfile, Job
from career_agent.scoring import constraint_evidence, score_job


class RemoteConstraintTests(unittest.TestCase):
    def setUp(self):
        self.job = Job('Fictional', 'Engineer', 'New York', 'https://example.invalid/jobs/1', remote=True)
        self.profile = {'require_remote': True}

    def review(self, description, *, remote=True):
        return constraint_evidence(replace(self.job, description=description, remote=remote), self.profile)[0]

    def test_hybrid_or_onsite_label_warns_about_remote_source_conflict(self):
        for line in ('Work arrangement: Hybrid', 'Workplace type: On-site', 'Work location type: On site'):
            with self.subTest(line=line):
                result = self.review(line)
                self.assertEqual(result['status'], 'flagged')
                self.assertIn(line, result['detail'])
                self.assertIn('signals conflict', result['detail'])
                self.assertIn('verify', result['detail'])
        self.assertEqual(self.review('Work arrangement: Hybrid', remote=False)['status'], 'flagged')

    def test_explicit_remote_label_is_not_a_geographic_eligibility_claim(self):
        row = self.review('Work arrangement: Remote', remote=False)
        self.assertEqual(row['status'], 'source_marked')
        self.assertIn('not confirmation of geographic eligibility', row['detail'])

    def test_ambiguous_labels_stay_unknown_and_prose_is_not_classified(self):
        for text in ('Work arrangement: Remote or Hybrid',
                     'Workplace type: Remote (US only)',
                     'Work arrangement: Remote\nWorkplace type: Hybrid',
                     'Work arrangement: Not remote'):
            with self.subTest(text=text):
                self.assertEqual(self.review(text)['status'], 'unknown')
        self.assertEqual(self.review('We support hybrid collaboration with other offices.', remote=False)['status'], 'unknown')
        self.assertEqual(self.review('No arrangement information', remote=True)['status'], 'source_marked')

    def test_conflicting_or_ambiguous_remote_signals_do_not_earn_remote_only_credit(self):
        profile = CandidateProfile(('Engineer',), (), (), True)
        baseline = replace(self.job, description='Work arrangement: Remote')
        self.assertEqual(score_job(baseline, profile).score, 50)
        for text in ('Work arrangement: Hybrid', 'Workplace type: On-site',
                     'Work arrangement: Remote or Hybrid',
                     'Work arrangement: Remote\nWorkplace type: Hybrid'):
            with self.subTest(text=text):
                fit = score_job(replace(self.job, description=text), profile)
                self.assertEqual(fit.score, 40)
                self.assertIn('remote credit withheld', ' '.join(fit.reasons))
                self.assertIn('verify', ' '.join(fit.reasons))
        self.assertEqual(score_job(replace(self.job, description='No arrangement label'), profile).score, 50)

    def test_preferred_location_can_match_but_conflicting_remote_is_disclosed(self):
        profile = CandidateProfile(('Engineer',), (), ('New York',), True)
        fit = score_job(replace(self.job, description='Work arrangement: Hybrid'), profile)
        self.assertEqual(fit.score, 50)
        self.assertIn('preferred location appears', ' '.join(fit.reasons))
        self.assertIn('remote signals conflict or are ambiguous', ' '.join(fit.reasons))

    def test_constraint_is_optional_and_does_not_change_score(self):
        job = replace(self.job, description='Work arrangement: Hybrid')
        profile = CandidateProfile(('Engineer',), (), ('New York',), True)
        before = score_job(job, profile)
        self.assertEqual(constraint_evidence(job, {}), [])
        self.review(job.description)
        self.assertEqual(score_job(job, profile), before)


if __name__ == '__main__':
    unittest.main()
