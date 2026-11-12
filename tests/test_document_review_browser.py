"""Browser exercise of fact-backed application review with fictional inputs."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from career_agent.models import CandidateProfile
from career_agent.storage import Store
from test_dashboard import fake_fact_bank, fake_job


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class DocumentReviewBrowserTests(unittest.TestCase):
    def test_tailoring_limit_is_visible_and_rejects_forty_one(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'workspace.sqlite3'
            data = fake_fact_bank()
            data['facts'] = [{**data['facts'][0], 'id': f'experience-{i:02d}',
                              'statement': f'Fictional achievement number {i:02d}.'}
                             for i in range(1, 42)]
            facts = root / 'facts.json'; facts.write_text(json.dumps(data))
            profile = root / 'profile.json'; profile.write_text(json.dumps(asdict(CandidateProfile(
                target_titles=('AI Program Lead',), skills=('Python',), preferred_locations=(), allow_remote=True))))
            with Store(db) as store: store.add_jobs([fake_job()])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', profile_path=profile, facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#job-list button').first.click()
                        page.get_by_text('Choose facts for a tailored draft', exact=True).click()
                        picker = page.locator('.tailoring-picker')
                        picker.get_by_role('button', name='Load verified facts').click()
                        expect(picker).to_contain_text('Choose up to 40 verified facts')
                        checkboxes = picker.locator('input[type=checkbox]')
                        expect(checkboxes).to_have_count(41)
                        for i in range(41): checkboxes.nth(i).check()
                        picker.get_by_role('button', name='Create tailored draft').click()
                        expect(picker).to_contain_text('Select between 1 and 40 facts.')
                        self.assertFalse((root / 'packets').exists())
                        checkboxes.nth(40).uncheck()
                        picker.get_by_role('button', name='Create tailored draft').click()
                        expect(page.locator('.review-card')).to_contain_text('Application packet', timeout=20000)
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_candidate_selects_verified_facts_for_new_review(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'workspace.sqlite3'
            data = fake_fact_bank()
            data['facts'].append({**data['facts'][0], 'id': 'experience-02',
                'statement': 'Led a fictional Python migration.', 'source_note': 'Private second note.'})
            data['facts'].append({**data['facts'][0], 'id': 'education-01', 'section': 'education',
                'statement': 'Completed a fictional degree.', 'source_note': 'Private education note.'})
            data['facts'].append({**data['facts'][0], 'id': 'projects-01', 'section': 'projects',
                'statement': 'Omitted fictional project for comparison.', 'source_note': 'Private project note.'})
            facts = root / 'facts.json'; facts.write_text(json.dumps(data))
            profile = root / 'profile.json'; profile.write_text(json.dumps(asdict(CandidateProfile(
                target_titles=('AI Program Lead',), skills=('Python',), preferred_locations=(), allow_remote=True))))
            with Store(db) as store: store.add_jobs([fake_job()])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', profile_path=profile, facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#job-list button').first.click()
                        page.get_by_text('Choose facts for a tailored draft', exact=True).click()
                        page.get_by_role('button', name='Load verified facts').click()
                        page.get_by_label('experience · Delivered a fictional AI program involving Python and AWS.').check()
                        page.get_by_label('experience · Led a fictional Python migration.').check()
                        page.get_by_label('education · Completed a fictional degree.').check()
                        page.get_by_role('button', name='Move fact experience-02 up').click()
                        page.get_by_role('button', name='Move education section up').click()
                        page.get_by_role('button', name='Move education section up').click()
                        page.get_by_role('button', name='Move education section up').click()
                        page.get_by_role('button', name='Create tailored draft').click()
                        evidence = page.locator('.packet-evidence')
                        expect(evidence).to_be_visible(); evidence.locator('summary').click()
                        expect(evidence).to_contain_text('Led a fictional Python migration.')
                        expect(evidence).not_to_contain_text('Private second note.')
                        expect(evidence).to_contain_text('experience-01')
                        comparison = page.locator('.resume-comparison').filter(has_text='Compare verified facts and tailored draft')
                        expect(comparison).to_be_visible(); comparison.locator('summary').click()
                        expect(comparison).to_contain_text('not your uploaded résumé')
                        expect(comparison).to_contain_text('projects-01 · projects · Omitted')
                        expect(comparison).to_contain_text('Omitted fictional project for comparison.')
                        expect(comparison).not_to_contain_text('Private project note.')
                        baseline = comparison.locator('section').nth(0).inner_text()
                        tailored = comparison.locator('section').nth(1).inner_text()
                        self.assertLess(baseline.index('experience-01'), baseline.index('education-01'))
                        self.assertLess(tailored.index('education-01'), tailored.index('experience-02'))
                        self.assertLess(tailored.index('experience-02'), tailored.index('experience-01'))
                        packet_text = page.locator('.review-card').get_by_text('Resume draft', exact=True)
                        packet_text.click()
                        resume = page.locator('.review-card details').filter(has_text='Resume draft').locator('pre').inner_text()
                        self.assertLess(resume.index('## Education'), resume.index('## Relevant Experience'))
                        self.assertLess(resume.index('Led a fictional Python migration.'), resume.index('Delivered a fictional AI program'))
                        page.get_by_text('I have personally reviewed this draft and its claims.').click()
                        page.get_by_role('button', name='Approve for manual submission').click()
                        expect(page.locator('.review-card')).to_contain_text('approved for manual submission')
                        page.reload(); page.locator('#job-list button').first.click()
                        expect(page.locator('.review-card')).to_contain_text('approved for manual submission')
                        page.get_by_text('Compare verified facts and tailored draft', exact=True).click()
                        expect(comparison).to_contain_text('projects-01 · projects · Omitted')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_draft_evidence_review_and_approval(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db, packet_dir = root / 'workspace.sqlite3', root / 'packets'
            facts, profile = root / 'facts.json', root / 'profile.json'
            facts.write_text(json.dumps(fake_fact_bank()))
            profile.write_text(json.dumps(asdict(CandidateProfile(target_titles=('AI Program Lead',),
                skills=('Python',), preferred_locations=(), allow_remote=True))))
            with Store(db) as store:
                store.add_jobs([fake_job()])
            app = create_app(DashboardConfig(db_path=db, packet_dir=packet_dir, profile_path=profile, facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#job-list button').first.click()
                        page.get_by_role('button', name='Create application draft').click()
                        evidence = page.locator('.packet-evidence')
                        expect(evidence).to_be_visible()
                        evidence.locator('summary').click()
                        expect(evidence).to_contain_text('experience-01')
                        expect(evidence).to_contain_text('Delivered a fictional AI program')
                        expect(evidence).not_to_contain_text('Private fictional evidence')
                        page.get_by_text('I have personally reviewed this draft and its claims.').click()
                        page.get_by_role('button', name='Approve for manual submission').click()
                        expect(page.locator('.review-card')).to_contain_text('approved for manual submission')
                        with page.expect_download() as received:
                            page.get_by_role('button', name='Download reviewed resume Word').click()
                        from docx import Document
                        downloaded = Document(received.value.path())
                        content = '\n'.join(item.text for item in downloaded.paragraphs)
                        self.assertIn('Delivered a fictional AI program', content)
                        self.assertNotIn('Private fictional evidence', content)
                        expect(page.locator('#notice')).to_contain_text('not tracked')
                        expect(page.locator('#detail-panel')).not_to_contain_text('Submission recorded')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
