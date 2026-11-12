"""Narrow URL comparison never establishes identity or changes stored jobs."""
import unittest

from career_agent.capture_matching import linkedin_capture_matches, linkedin_job_number


class LinkedInCaptureMatchingTests(unittest.TestCase):
    def test_numeric_and_slug_urls_compare_only_the_job_number(self):
        numeric = 'https://www.linkedin.com/jobs/view/1234567890'
        for url in ('https://www.linkedin.com/jobs/view/fictional-engineer-1234567890/',
                    'https://linkedin.com/jobs/view/1234567890?refId=fictional&trackingId=abc',
                    'https://www.linkedin.com/jobs/view/fictional-1234567890?currentJobId=1234567890'):
            with self.subTest(url=url):
                self.assertTrue(linkedin_capture_matches(numeric, url))
        self.assertFalse(linkedin_capture_matches(numeric, 'https://www.linkedin.com/jobs/view/engineer-1234567891'))

    def test_ambiguous_or_untrusted_urls_do_not_match(self):
        for url in ('http://www.linkedin.com/jobs/view/123', 'https://user:pass@linkedin.com/jobs/view/123',
                    'https://linkedin.com.evil.invalid/jobs/view/123', 'https://linkedin.com:444/jobs/view/123',
                    'https://linkedin.com/jobs/view/123#another', 'https://linkedin.com/jobs/search/?currentJobId=123',
                    'https://linkedin.com/jobs/view/123?currentJobId=456',
                    'https://linkedin.com/jobs/view/123?currentJobId=123&currentJobId=123',
                    'https://linkedin.com/jobs/view/123?jobId=456', 'https://linkedin.com/jobs/view/123?ref=456',
                    'https://linkedin.com/jobs/view/role-123/extra', 'https://linkedin.com/jobs/view/%31%32%33'):
            with self.subTest(url=url):
                self.assertIsNone(linkedin_job_number(url))


if __name__ == '__main__':
    unittest.main()
