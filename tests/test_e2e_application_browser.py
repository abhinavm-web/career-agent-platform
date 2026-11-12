"""A complete fictional local application-preparation journey; no external submission."""
import os
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ApplicationJourneyTests(unittest.TestCase):
    def test_discovery_sponsorship_and_ai_evidence_journey(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from test_dashboard import fake_fact_bank

        job = Job.from_mapping({'company': 'Fictional Analytics', 'title': 'Data Analyst',
            'job_url': 'https://example.invalid/job/analytics', 'description': 'Python skills. H-1B sponsorship available.'})
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        output = {'requirements': [{'quote': 'Python skills.', 'fact_ids': ['experience-01'], 'reason': 'Review relevance.'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); facts = root / 'facts.json'; facts.write_text(json.dumps(fake_fact_bank()))
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets', facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with patch('career_agent.dashboard.discover_isolated', return_value=[job]) as discover, \
                     patch('career_agent.ai.routes', return_value=route), \
                     patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_text('Find jobs with JobSpy', exact=True).click()
                        page.locator('#discover-query').fill('Data Analyst')
                        page.locator('#discover-consent').check()
                        page.locator('#discover-submit').click()
                        expect(page.locator('#discover-status')).to_contain_text('1 returned', timeout=20000)
                        expect(page.locator('#job-list')).to_contain_text('Data Analyst')
                        self.assertEqual(discover.call_count, 1)
                        page.locator('#nav-h1b').click()
                        expect(page.locator('#h1b-opportunities-results')).to_contain_text('H-1B sponsorship available.')
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='View Data Analyst at Fictional Analytics').click()
                        page.get_by_role('button', name='Analyze with AI').click()
                        expect(page.locator('#ai-run')).to_be_enabled()
                        complete.assert_not_called()
                        page.locator('#ai-consent').check(); page.locator('#ai-run').click()
                        expect(page.locator('#ai-results')).to_contain_text('Python skills.')
                        expect(page.locator('#ai-results')).to_contain_text('Delivered a fictional AI program')
                        self.assertEqual(complete.call_count, 1)
                        complete.return_value = json.dumps({'suggestions': [{'fact_id': 'experience-01',
                            'job_quote': 'Python skills.', 'wording': 'A fictional suggested rewrite.'}]})
                        page.locator('#ai-consent').check(); page.locator('#ai-tailor').click()
                        expect(page.locator('#ai-results')).to_contain_text('Unverified suggested wording: A fictional suggested rewrite.')
                        expect(page.locator('#ai-results')).to_contain_text('Delivered a fictional AI program')
                        expect(page.locator('#ai-results')).to_contain_text('do not change your profile')
                        self.assertEqual(complete.call_count, 2)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_verified_facts_to_reviewed_application_survive_reload(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Store(root / 'fictional.sqlite3') as store:
                store.add_jobs([Job.from_mapping({'company': 'Other Fictional Co', 'title': 'Other role',
                    'job_url': 'https://example.invalid/jobs/other'})])
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-profile').click()
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.locator('#career-targets').fill('Data Analyst')
                        page.locator('#career-skills').fill('Python')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.get_by_text('Import and review résumé text', exact=True).click()
                        page.locator('#resume-text').fill('Experience:\nBuilt a fictional Python analytics dashboard.')
                        page.locator('#resume-extract').click()
                        expect(page.locator('#resume-lines')).to_contain_text('Suggested experience')
                        page.locator('#resume-use-line').click()
                        expect(page.locator('#fact-statement')).to_have_value('Built a fictional Python analytics dashboard.')
                        expect(page.locator('#career-facts-list')).to_contain_text('No career facts yet')
                        page.locator('#fact-source').fill('Fictional private source note.')
                        page.locator('#fact-keywords').fill('Python')
                        page.get_by_role('button', name='Save unverified fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified')

                        page.locator('#nav-jobs').click()
                        page.locator('#show-add-job').click()
                        page.locator('#add-job-form [name="title"]').fill('Data Analyst')
                        page.locator('#add-job-form [name="company"]').fill('Fictional Analytics')
                        page.locator('#add-job-form [name="location"]').fill('Remote USA')
                        page.locator('#add-job-form [name="job_url"]').fill('https://example.invalid/jobs/fictional')
                        page.locator('#add-job-form [name="description"]').fill('Required qualifications:\n- Python data analysis\n- Rust experience')
                        page.get_by_role('button', name='Save opportunity').click()
                        expect(page.locator('#job-list')).to_contain_text('Data Analyst')
                        page.locator('#job-list button').first.click()
                        page.get_by_role('button', name='Compare requirements').click()
                        expect(page.locator('#fit-evidence')).to_contain_text('Built a fictional Python analytics dashboard.')
                        expect(page.locator('#fit-evidence')).to_contain_text('evidence remains unknown')
                        page.get_by_role('button', name='Create application draft').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Application packet', timeout=20000)
                        expect(page.locator('#detail-panel')).not_to_contain_text('Fictional private source note')
                        page.get_by_text('Resume draft', exact=True).click()
                        expect(page.locator('#detail-panel')).to_contain_text('Built a fictional Python analytics dashboard.')
                        approve = page.get_by_role('button', name='Approve for manual submission')
                        expect(approve).to_be_disabled()
                        page.get_by_label('I have personally reviewed this draft and its claims.').check()
                        approve.click()
                        expect(page.locator('#detail-panel')).to_contain_text('approved for manual submission', timeout=20000)
                        page.get_by_text('Correct saved description', exact=True).click()
                        page.get_by_label('Corrected job description').fill('Python data analysis. No visa sponsorship.')
                        page.get_by_label('Source note (optional)').fill('Fictional posting reviewed today')
                        page.get_by_label('I reviewed this corrected description.').check()
                        old_heading = page.locator('#detail-panel h3').element_handle()
                        page.locator('#refresh').click()
                        page.wait_for_function('(element) => !element.isConnected', arg=old_heading)
                        expect(page.get_by_label('Corrected job description')).to_have_value('Python data analysis. No visa sponsorship.')
                        expect(page.get_by_label('Source note (optional)')).to_have_value('Fictional posting reviewed today')
                        expect(page.get_by_label('I reviewed this corrected description.')).to_be_checked()
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='View Other role at Other Fictional Co').click()
                        expect(page.get_by_label('Corrected job description')).to_have_value('Python data analysis. No visa sponsorship.')
                        page.get_by_role('button', name='Save corrected description').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Posting excludes sponsorship')
                        expect(page.locator('#detail-panel')).to_contain_text('job description changed')
                        page.get_by_role('button', name='Regenerate draft').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Application packet', timeout=20000)
                        page.get_by_label('I have personally reviewed this draft and its claims.').check()
                        page.get_by_role('button', name='Approve for manual submission').click()
                        expect(page.locator('#detail-panel')).to_contain_text('approved for manual submission', timeout=20000)
                        page.reload()
                        page.locator('#job-list button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('approved for manual submission', timeout=20000)
                        expect(page.locator('#detail-panel')).to_contain_text('Already applied on employer site?')
                        expect(page.locator('#count-submitted')).to_have_text('0')
                        page.get_by_label('Employer confirmation reference').fill('fictional-receipt-123')
                        page.get_by_label('I personally submitted this application externally.').check()
                        page.get_by_role('button', name='Record confirmed submission').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Submission recorded from your attestation', timeout=20000)
                        expect(page.locator('#count-submitted')).to_have_text('1')
                        page.locator('#nav-tracker').click()
                        expect(page.locator('#tracker-screen')).to_contain_text('Data Analyst')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
