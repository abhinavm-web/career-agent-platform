"""Offline checks for explicit, bounded DOL release discovery."""
import unittest
from unittest.mock import patch

from career_agent.dol_releases import check_dol_releases
from career_agent.h1b import DOL_URL
from career_agent.public_sources import SourceUnavailable


class DOLReleaseTests(unittest.TestCase):
    def test_selects_latest_supported_official_link_without_fetching_it(self):
        page = b'''<a href="/media/LCA_Disclosure_Data_FY2025_Q4.xlsx">Old</a>
          <a href="https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx">Current</a>
          <a href="https://evil.invalid/media/LCA_Disclosure_Data_FY2026_Q4.xlsx">Untrusted</a>
          <a href="/media/LCA_Disclosure_Data_FY2026_Q4.xlsx?redirect=1">Query</a>
          <a href="/media/LCA_Disclosure_Data_FY2099_Q4.xlsx">Future</a>'''
        with patch('career_agent.dol_releases.fiscal_year', return_value=2026), \
             patch('career_agent.dol_releases.public_get', return_value=(200, page, 'text/html; charset=utf-8')) as get:
            result = check_dol_releases()
        self.assertEqual((result['fiscal_year'], result['quarter']), (2026, 3))
        self.assertEqual(result['workbook_url'], 'https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx')
        get.assert_called_once_with(DOL_URL, {'www.dol.gov'}, maximum=2 * 1024 * 1024)
        self.assertIn('not independently verified', result['notice'])

    def test_403_wrong_type_empty_and_ambiguous_links_fail_closed(self):
        with patch('career_agent.dol_releases.public_get') as get:
            for response in [(403, b'', 'text/html'), (200, b'{}', 'application/json'),
                             (200, b'<html>no release</html>', 'text/html'),
                             (200, b'\xff', 'text/html')]:
                with self.subTest(response=response), self.assertRaises(SourceUnavailable):
                    get.return_value = response
                    check_dol_releases()
            get.return_value = (200, b'<a href="https://dol.gov/media/LCA_Disclosure_Data_FY2025_Q3.xlsx">x</a><a href="/media/LCA_Disclosure_Data_FY2025_Q3.xlsx">y</a>', 'text/html')
            with self.assertRaisesRegex(SourceUnavailable, 'multiple links'):
                check_dol_releases()


if __name__ == '__main__':
    unittest.main()
