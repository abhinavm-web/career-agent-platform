"""Browser coverage for the Résumé tab: original beside recommended, wizard-driven additions."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ORIGINAL = 'Fictional Candidate\nDelivered a fictional AI program involving Python and AWS.'


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ResumeCompareBrowserTests(unittest.TestCase):
    def test_original_beside_recommended_with_wizard_additions(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent import ai
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import CandidateProfile, Job
        from career_agent.storage import Store
        def fake_model(route_config, prompt, *, system=None, json_schema=None):
            if system == ai.EXPERIENCE_QUESTION_SYSTEM:
                return 'Where did you report to executives, and how often?'
            excerpt_id = next(key for key, text in json.loads(prompt)['source_excerpts'].items() if 'Kubernetes' in text)
            return json.dumps({'suggestions': [{'fact_id': 'skills-01', 'excerpt_id': excerpt_id, 'wording': 'x'}]})
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            profile = CandidateProfile(target_titles=('AI Program Lead',), skills=('Python',), preferred_locations=(), allow_remote=True)
            (root / 'preferences.json').write_text(json.dumps(asdict(profile)))
            (root / 'facts.json').write_text(json.dumps({'name': 'Fictional Candidate', 'headline': 'AI', 'facts': [
                {'id': 'experience-01', 'section': 'experience', 'verified_by_candidate': True, 'source_note': 'Fictional.',
                 'statement': 'Delivered a fictional AI program involving Python and AWS.', 'keywords': ['Python', 'AWS']},
                {'id': 'projects-01', 'section': 'projects', 'verified_by_candidate': True, 'source_note': 'Fictional.',
                 'statement': 'Built a fictional Kubernetes platform for batch jobs.', 'keywords': ['Kubernetes']},
                {'id': 'skills-01', 'section': 'skills', 'verified_by_candidate': True, 'source_note': 'Fictional.',
                 'statement': 'Operated fictional batch scheduling systems.', 'keywords': ['Scheduling']}]}))
            with Store(db) as store:
                store.add_jobs([Job('Fictional Co', 'Fictional AI Lead', 'Boston', 'https://example.invalid/role',
                    'Requirements\n- Python and AWS delivery\n- Kubernetes operations\n- Executive stakeholder reporting\n')])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets',
                                             profile_path=root / 'preferences.json', facts_path=root / 'facts.json'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=route), \
                        patch('career_agent.ai.complete', side_effect=fake_model), \
                        sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 900})
                        page.goto(f'http://127.0.0.1:{port}/classic')
                        page.evaluate('''async () => { await fetch('/api/workspace/import', {method: 'POST', body: '{"acknowledged": true}',
                            headers: {'X-Session-Token': document.getElementById('app').dataset.session, 'Content-Type': 'application/json'}}); }''')
                        page.reload()
                        page.get_by_role('button', name='Opportunity board').click()
                        page.get_by_role('button', name='View Fictional AI Lead at Fictional Co').click()
                        page.get_by_role('tab', name='Résumé').click()

                        # No disclosure widgets: everything is visible.
                        expect(page.get_by_text('Add your current résumé once')).to_be_visible()
                        self.assertEqual(page.locator('#job-pane-resume details').count(), 0)
                        page.locator('.rc-original textarea').fill(ORIGINAL)
                        page.get_by_role('button', name='Save original résumé').click()
                        expect(page.get_by_text('Confirm saving the text locally.')).to_be_visible()
                        page.get_by_label('Save this text in my local workspace', exact=False).check()
                        page.get_by_role('button', name='Save original résumé').click()

                        original, recommended = page.locator('.rc-original'), page.locator('.rc-recommended')
                        expect(original).to_contain_text(ORIGINAL.splitlines()[1])
                        expect(recommended.locator('.rc-in')).to_contain_text('Delivered a fictional AI program')
                        expect(recommended.locator('.rc-added')).to_contain_text('Built a fictional Kubernetes platform')
                        left, right = original.bounding_box(), recommended.bounding_box()
                        self.assertLess(left['x'], right['x'])                      # side by side
                        self.assertAlmostEqual(left['y'], right['y'], delta=2)
                        self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)

                        # AI may only choose among verified facts; its picks are labelled for review.
                        expect(recommended).not_to_contain_text('batch scheduling')
                        page.get_by_role('button', name='Find more matches with AI').click()
                        recommended.get_by_role('button', name='Choose an AI model').click()
                        recommended.get_by_label('I consent to sending this posting', exact=False).check()
                        recommended.get_by_role('button', name='Continue').click()
                        suggested = recommended.locator('.rc-tag-ai').first
                        expect(suggested).to_have_text('AI match · review')
                        expect(recommended).to_contain_text('Operated fictional batch scheduling systems.')
                        expect(recommended).to_contain_text('Matched to: - Kubernetes operations')

                        # Wizard: one step at a time, AI optional, explicit confirmation required.
                        expect(page.locator('.rc-steps li.current')).to_have_text('Choose a requirement')
                        page.get_by_role('button', name='Executive stakeholder reporting').click()
                        expect(page.locator('.rc-steps li.current')).to_have_text('Describe what you did')
                        page.get_by_role('button', name='Ask AI for a sharper question').click()
                        page.get_by_role('button', name='Choose an AI model').click()
                        page.get_by_label('I consent to sending this requirement', exact=False).check()
                        page.get_by_role('button', name='Continue').click()
                        expect(page.locator('.rc-question')).to_have_text('Where did you report to executives, and how often?')
                        page.locator('.rc-step textarea').fill('Delivered fictional stakeholder reporting to executives monthly.')
                        page.get_by_role('button', name='Next: review').click()
                        expect(page.locator('.rc-steps li.current')).to_have_text('Confirm')
                        expect(page.locator('.rc-final')).to_have_text('Delivered fictional stakeholder reporting to executives monthly.')
                        add = page.get_by_role('button', name='Add to my résumé')
                        expect(add).to_be_disabled()
                        page.get_by_label('I checked this statement', exact=False).check()
                        add.click()
                        expect(recommended.locator('.rc-added').filter(has_text='stakeholder reporting')).to_contain_text('Answers: Executive stakeholder reporting')
                        expect(page.get_by_role('button', name='Executive stakeholder reporting')).to_have_count(0)

                        page.set_viewport_size({'width': 390, 'height': 844})
                        stacked_left, stacked_right = original.bounding_box(), recommended.bounding_box()
                        self.assertLess(stacked_left['y'], stacked_right['y'])
                        self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
