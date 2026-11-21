"""A first-time user can go from nothing to downloadable documents without any setup."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from career_agent.models import Job

RESUME = ("Alex Fictional\nalex@example.invalid | 555-123-4567\nEXPERIENCE\n"
          "• Led a fictional budget process for a team of four.\n• Built a fictional AI tool in Python for the front desk.\n"
          "Education\nB.S. Fictional Studies, Example University\nSkills\nPython, customer service, AWS")


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class SimpleJourneyTests(unittest.TestCase):
    def test_new_user_journey_to_documents(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent import ai
        from career_agent.dashboard import DashboardConfig, create_app
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        letter = 'Dear Hiring Team,\n\nI built an AI tool in Python for the front desk.\n\nSincerely,\nAlex Fictional'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                found = [Job.from_mapping({'company': 'Example Clinic', 'title': 'Front Desk AI Assistant', 'location': 'Knoxville, TN',
                    'job_url': 'https://example.invalid/jobs/front-desk',
                    'description': 'Requirements\n- Experience with Python\n- Strong customer service skills\n- Budget awareness'})]
                with patch('career_agent.ai.routes', return_value=route), \
                        patch('career_agent.dashboard.discover_isolated', return_value=found) as discover, \
                        patch('career_agent.ai.complete', return_value=letter) as complete, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(viewport={'width': 1280, 'height': 900})
                        page.goto(f'http://127.0.0.1:{port}')

                        # Nothing set up yet: the app starts the wizard by itself.
                        expect(page.get_by_role('heading', name='Set up your résumé')).to_be_visible()
                        page.get_by_role('button', name='Next').click()
                        expect(page.get_by_text('Add your résumé first')).to_be_visible()
                        page.get_by_label('Paste your résumé here').fill(RESUME)
                        page.get_by_role('button', name='Next').click()
                        expect(page.get_by_label('Your name')).to_have_value('Alex Fictional')
                        page.get_by_role('button', name='Next').click()

                        # One line at a time; saying no keeps a line out for good.
                        expect(page.get_by_text('Line 1 of 4')).to_be_visible()
                        page.get_by_role('button', name='Yes, this is true').click()
                        page.get_by_role('button', name='Yes, this is true').click()
                        expect(page.get_by_text('B.S. Fictional Studies, Example University')).to_be_visible()
                        page.get_by_role('button', name='No, skip it').click()
                        page.get_by_role('button', name='Yes, this is true').click()
                        expect(page.get_by_role('heading', name='You said 3 of 4 lines are true')).to_be_visible()
                        page.get_by_role('button', name='Save and find a job').click()

                        # Jobs: the app searches for you; nobody has to type a job in.
                        expect(page.get_by_role('heading', name='Find a job to apply for')).to_be_visible()
                        expect(page.get_by_role('button', name='Add a job')).to_have_count(0)
                        find = page.get_by_role('button', name='Find jobs')
                        expect(find).to_be_disabled()
                        page.get_by_label('What kind of job?').fill('front desk')
                        page.get_by_label('Where?').fill('Knoxville, TN')
                        expect(find).to_be_disabled()                      # nothing is sent without the tick
                        discover.assert_not_called()
                        page.get_by_label('It is OK to search Indeed and ZipRecruiter', exact=False).check()
                        find.click()
                        expect(page.locator('#search-note')).to_contain_text('Found 1 job')
                        self.assertEqual(discover.call_count, 1)
                        self.assertEqual(discover.call_args.args[:2], ('front desk', 'Knoxville, TN'))
                        expect(page.locator('.card .lead')).to_contain_text('front desk in Knoxville, TN')   # remembered
                        expect(page.get_by_label('It is OK to search Indeed and ZipRecruiter', exact=False)).not_to_be_checked()  # one tick per search
                        page.reload()
                        expect(page.locator('.card .lead')).to_contain_text('front desk in Knoxville, TN')   # still remembered
                        page.get_by_role('button', name='Front Desk AI Assistant').click()

                        # One page per job: original and recommended side by side, then the letter.
                        expect(page.get_by_role('heading', name='Front Desk AI Assistant')).to_be_visible()
                        papers = page.locator('.papers .paper')
                        expect(papers).to_have_count(2)
                        left, right = papers.nth(0).bounding_box(), papers.nth(1).bounding_box()
                        self.assertLess(left['x'], right['x'])
                        self.assertAlmostEqual(left['y'], right['y'], delta=2)
                        expect(papers.nth(1)).not_to_contain_text('B.S. Fictional Studies')   # a skipped line is never used
                        expect(papers.nth(0)).to_contain_text('B.S. Fictional Studies')       # but the original is untouched
                        expect(page.locator('#letter-paper')).to_contain_text('Dear Hiring Team,')
                        self.assertEqual(page.locator('details').count(), 0)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

                        # Add something true that this job asks for; it shows up marked NEW.
                        expect(page.get_by_text('This job asks for:')).to_contain_text('Budget awareness')
                        page.get_by_label('Something true you did that fits this job').fill('Managed a fictional school office budget for two years.')
                        page.get_by_role('button', name='Add it to my résumé').click()
                        expect(page.get_by_text('Tick the box to say it is true.')).to_be_visible()
                        page.get_by_label('This is true about me.').check()
                        page.get_by_role('button', name='Add it to my résumé').click()
                        expect(papers.nth(1).locator('li.new')).to_contain_text('Managed a fictional school office budget')

                        # Downloads work with no extra setup.
                        with page.expect_download() as resume_download:
                            page.get_by_role('button', name='Download résumé (Word)').click()
                        self.assertEqual(resume_download.value.suggested_filename, 'resume.docx')
                        with page.expect_download() as letter_download:
                            page.get_by_role('button', name='Download cover letter (Word)').click()
                        self.assertEqual(letter_download.value.suggested_filename, 'cover-letter.docx')
                        expect(page.get_by_role('link', name='Open the job posting to apply')).to_have_attribute('rel', 'noopener noreferrer')

                        # Optional AI letter: needs consent, replaces only the letter.
                        complete.assert_not_called()
                        page.get_by_role('button', name='Write a better letter with AI').click()
                        page.get_by_label('Send my true résumé lines and this job', exact=False).check()
                        page.get_by_role('button', name='Write my letter').click()
                        expect(page.get_by_text('Written by AI from your true lines')).to_be_visible()
                        expect(page.locator('#letter-paper')).to_contain_text('built an AI tool in Python')
                        self.assertEqual(complete.call_count, 1)

                        # A phone gets the same page, stacked, with no sideways scrolling.
                        page.set_viewport_size({'width': 390, 'height': 844})
                        stacked = papers.nth(0).bounding_box(), papers.nth(1).bounding_box()
                        self.assertLess(stacked[0]['y'], stacked[1]['y'])
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

                        # Backup: paste a posting found elsewhere; title and company are guessed.
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='Found a job somewhere else? Paste it instead.').click()
                        page.get_by_label('The job posting').fill('Library Helper\nAbout Example Library\nRequirements\n- Helping people find books\n- Organizing shelves')
                        page.get_by_role('button', name='Use this job').click()
                        expect(page.get_by_role('heading', name='Library Helper')).to_be_visible()
                        expect(page.get_by_text('Example Library', exact=False).first).to_be_visible()
                        expect(page.get_by_role('link', name='Open the job posting to apply')).to_have_count(0)   # no link was given

                        # Everything else is still one click away.
                        page.get_by_role('link', name='More tools').click()
                        expect(page).to_have_url(f'http://127.0.0.1:{port}/classic')
                        expect(page.get_by_role('heading', name='Search overview')).to_be_visible()
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
