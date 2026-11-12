"""Fictional profile and inline-AI workflows; no real provider calls."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class InlineGuidedTests(unittest.TestCase):
    def test_guided_steps_preserve_inputs_and_require_verification(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_role('button', name='Career profile', exact=True).click()
                        page.get_by_role('button', name='Start guided setup').click()
                        expect(page.locator('#profile-wizard-heading')).to_have_text('1 of 3 · Preferences')
                        page.locator('#career-name').fill('Fictional Candidate')
                        page.get_by_role('button', name='Next step').click()
                        expect(page.locator('#profile-wizard-heading')).to_have_text('2 of 3 · Import a résumé')
                        page.locator('#resume-text').fill('Experience:\nBuilt a fictional analytics dashboard.')
                        page.get_by_role('button', name='Find fact suggestions').click()
                        page.get_by_role('button', name='Review as a career fact').click()
                        expect(page.locator('#profile-wizard-heading')).to_have_text('3 of 3 · Review and verify facts')
                        expect(page.locator('#fact-statement')).to_have_value('Built a fictional analytics dashboard.')
                        page.locator('#fact-source').fill('Fictional source')
                        page.get_by_role('button', name='Save unverified fact').click()
                        fact = page.locator('.career-fact').filter(has_text='Built a fictional analytics dashboard.')
                        expect(fact).to_contain_text('Not verified')
                        with Store(db) as store:
                            snapshot = CandidateWorkspace(store).latest()
                            self.assertFalse(snapshot['facts'][0]['verified_by_candidate'])
                        page.get_by_role('button', name='Previous step').click()
                        page.get_by_role('button', name='Previous step').click()
                        expect(page.locator('#career-name')).to_have_value('Fictional Candidate')
                        page.get_by_role('button', name='Save preferences').click()
                        expect(page.locator('#notice')).to_contain_text('Preferences saved')
                        page.get_by_role('button', name='Next step').click()
                        page.get_by_role('button', name='Next step').click()
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.locator('.career-fact').filter(has_text='Built a fictional analytics dashboard.').get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('.career-fact').filter(has_text='Built a fictional analytics dashboard.')).to_contain_text('Candidate verified')
                        page.get_by_role('button', name='Exit guided setup').click()
                        expect(page.locator('#career-profile-form')).to_be_visible()
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_inline_tailoring_requires_consent_and_stages_unverified_wording(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from test_dashboard import fake_fact_bank
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            profile = root / 'profile.json'; profile.write_text(json.dumps({'name': 'Fictional Candidate',
                'target_titles': ['Data Analyst'], 'skills': ['Python'], 'preferred_locations': [], 'allow_remote': True}))
            facts = root / 'facts.json'; facts.write_text(json.dumps(fake_fact_bank()))
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Data Analyst',
                'job_url': 'https://example.invalid/role', 'description': 'Python skills.'})
            other = Job.from_mapping({'company': 'Other Fictional Co', 'title': 'Other role',
                'job_url': 'https://example.invalid/other', 'description': 'Other fictional role.'})
            with Store(db) as store: store.add_jobs([job, other])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', profile_path=profile, facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'},
                     'other': {'provider': 'ollama', 'model': 'second-fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
            output = {'suggestions': [{'fact_id': 'experience-01', 'job_quote': 'Python skills.',
                                       'wording': 'Delivered a fictional Python program.'}]}
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_role('button', name='Career profile', exact=True).click()
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Import configured files').click()
                        expect(page.locator('#notice')).to_contain_text('imported')
                        page.get_by_role('button', name='Opportunity board').click()
                        page.get_by_role('button', name='View Data Analyst at Fictional Co').click()
                        page.get_by_role('tab', name='Application').click()
                        page.get_by_text('Compare AI wording with verified facts').click()
                        panel = page.locator('#inline-tailoring')
                        panel.get_by_role('button', name='Load model routes').click()
                        expect(panel).to_contain_text('ollama · fictional')
                        panel.get_by_role('button', name='Suggest wording').click()
                        complete.assert_not_called()
                        panel.get_by_role('checkbox').check()
                        panel.get_by_label('Inline model route').select_option('other')
                        expect(panel.get_by_role('checkbox')).not_to_be_checked()
                        panel.get_by_label('Inline model route').select_option('local')
                        panel.get_by_role('checkbox').check()
                        panel.get_by_role('button', name='Suggest wording').click()
                        expect(panel).to_contain_text('Delivered a fictional AI program involving Python and AWS.')
                        expect(panel).to_contain_text('Delivered a fictional Python program.')
                        expect(panel.get_by_role('checkbox')).not_to_be_checked()
                        self.assertEqual(complete.call_count, 1)
                        old_heading = page.locator('#detail-panel h3').element_handle()
                        page.locator('#refresh').click()
                        page.wait_for_function('(element) => !element.isConnected', arg=old_heading)
                        expect(panel).to_contain_text('Delivered a fictional Python program.')
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='View Other role at Other Fictional Co').click()
                        expect(panel).to_contain_text('Delivered a fictional Python program.')
                        panel.get_by_role('button', name='Save unverified wording for review').click()
                        expect(panel).to_contain_text('Saved as an unverified fact')
                        with Store(db) as store:
                            snapshot = CandidateWorkspace(store).latest()
                            staged = next(f for f in snapshot['facts'] if f['statement'] == output['suggestions'][0]['wording'])
                            self.assertFalse(staged['verified_by_candidate'])
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
