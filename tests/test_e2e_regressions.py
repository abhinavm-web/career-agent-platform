"""Fictional posting language from the real-workflow failure categories."""
import unittest
from career_agent.sponsorship import h1b_policy

class SponsorshipWordingTests(unittest.TestCase):
    def test_benefit_sponsorship_is_not_visa_sponsorship(self):
        for text in ('Example sponsored benefit programs.', 'Company sponsored events and conferences.', 'Employer sponsored retirement plans.'):
            with self.subTest(text=text):
                self.assertEqual(h1b_policy(text), h1b_policy('No visa information here.'))

    def test_benefits_do_not_hide_a_real_visa_policy_in_same_sentence(self):
        for text in ('We offer sponsored benefits but cannot sponsor H-1B visas.', 'We offer sponsored benefits and provide visa sponsorship.'):
            policy = h1b_policy(text)
            self.assertNotEqual(policy['policy'], h1b_policy('No visa information here.')['policy'])
