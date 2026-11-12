"""Fictional legal-entity review and reuse through the browser."""
import io
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from career_agent.h1b import DOL_URL, H1BResearch
from career_agent.models import Job
from career_agent.storage import Store
from test_h1b import lca_csv


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class EmployerResolutionBrowserTests(unittest.TestCase):
    def test_broader_name_search_requires_separate_association_confirmation(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Engineer',
                'job_url': 'https://example.invalid/engineer', 'description': 'Python role.'})
            with Store(db) as store:
                store.add_jobs([job])
                H1BResearch(store).import_csv(io.StringIO(lca_csv([{'EMPLOYER_NAME': 'Fictional, Co LLC'}])), DOL_URL, '2025-06-01')
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
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
                        page.locator('#nav-h1b').click()
                        card = page.locator('#h1b-opportunities-results > section').filter(has_text='Engineer')
                        card.get_by_text('Associate an imported legal employer').click()
                        card.get_by_role('button', name='Search imported employer names').click()
                        expect(card).to_contain_text('Showing 0–0 of 0')
                        card.get_by_label('Ignore punctuation and spaces when finding possible names').check()
                        card.get_by_role('button', name='Search imported employer names').click()
                        card.get_by_role('button', name='Use Fictional, Co LLC').click()
                        expect(card.get_by_label('Imported legal employer name')).to_have_value('Fictional, Co LLC')
                        expect(card.get_by_label('I understand this association is not independently verified.')).not_to_be_checked()
                        with Store(db) as store:
                            self.assertIsNone(H1BResearch(store).employer_link(job.id))
                        card.get_by_label('How you checked the association').fill('Fictional entity reviewed')
                        card.get_by_label('I understand this association is not independently verified.').check()
                        card.get_by_role('button', name='Save association').click()
                        expect(card).to_contain_text('Candidate-provided employer association, not independently verified')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_saved_suggestion_requires_each_job_to_be_confirmed(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': title,
                    'job_url': 'https://example.invalid/' + slug, 'description': 'Python role.'})
                    for title, slug in [('First role', 'first'), ('Second role', 'second')]])
                names = [{'EMPLOYER_NAME': 'Fictional Co LLC'}] + [
                    {'EMPLOYER_NAME': f'Fictional Co Z {i:02} LLC'} for i in range(26)]
                H1BResearch(store).import_csv(io.StringIO(lca_csv(names)), DOL_URL, '2025-06-01')
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
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
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        first = page.locator('#h1b-opportunities-results .panel').filter(has_text='First role')
                        expect(first).to_be_visible()
                        first.get_by_text('Associate an imported legal employer', exact=True).click()
                        first.get_by_role('button', name='Search imported employer names').click()
                        expect(first.get_by_role('button', name='Use Fictional Co LLC')).to_be_visible()
                        expect(first).to_contain_text('Showing 1–25 of 27')
                        first.get_by_role('button', name='Next employer names').click()
                        expect(first).to_contain_text('Showing 26–27 of 27')
                        expect(first.get_by_role('button', name='Use Fictional Co Z 25 LLC')).to_be_visible()
                        expect(first.get_by_label('Imported legal employer name')).to_have_value('')
                        first.get_by_role('button', name='Use Fictional Co Z 25 LLC').click()
                        expect(first.get_by_label('Imported legal employer name')).to_have_value('Fictional Co Z 25 LLC')
                        expect(first.get_by_label('I understand this association is not independently verified.')).not_to_be_checked()
                        first.get_by_role('button', name='Previous employer names').click()
                        expect(first.get_by_role('button', name='Use Fictional Co LLC')).to_be_visible()
                        first.get_by_label('Find possible filing names').fill('Other')
                        expect(first.get_by_role('button', name='Use Fictional Co LLC')).to_have_count(0)
                        first.get_by_label('Find possible filing names').fill('Fictional Co')
                        first.get_by_role('button', name='Search imported employer names').click()
                        first.get_by_role('button', name='Use Fictional Co LLC').click()
                        first.get_by_label('How you checked the association').fill('Fictional legal entity reviewed')
                        first.get_by_label('I understand this association is not independently verified.').check()
                        first.get_by_role('button', name='Save association').click()
                        expect(first).to_contain_text('Candidate-provided employer association, not independently verified')
                        first.get_by_text('Associate an imported legal employer', exact=True).click()
                        page.once('dialog', lambda dialog: dialog.accept())
                        first.get_by_role('button', name='Save as suggestion for this company name').click()
                        expect(first).to_contain_text('Previously saved suggestion')
                        page.get_by_role('button', name='Find opportunities', exact=True).click()
                        second = page.locator('#h1b-opportunities-results .panel').filter(has_text='Second role')
                        expect(second).to_contain_text('Historical DOL lookup for Fictional Co: No exact-name DOL LCA records')
                        expect(second).not_to_contain_text('Candidate-provided employer association')
                        second.get_by_text('Associate an imported legal employer', exact=True).click()
                        second.get_by_role('button', name='Review saved suggestion').click()
                        expect(second.get_by_label('Imported legal employer name')).to_have_value('Fictional Co LLC')
                        expect(second.get_by_label('I understand this association is not independently verified.')).not_to_be_checked()
                        second.get_by_label('I understand this association is not independently verified.').check()
                        second.get_by_role('button', name='Save association').click()
                        expect(second).to_contain_text('Candidate-provided employer association, not independently verified')
                        page.reload(); page.get_by_role('button', name='H-1B research', exact=True).click()
                        second = page.locator('#h1b-opportunities-results .panel').filter(has_text='Second role')
                        expect(second).to_contain_text('Fictional Co LLC')
                        second.get_by_text('Associate an imported legal employer', exact=True).click()
                        page.once('dialog', lambda dialog: dialog.accept())
                        second.get_by_role('button', name='Remove saved suggestion').click()
                        expect(second).not_to_contain_text('Previously saved suggestion')
                        expect(second).to_contain_text('Candidate-provided employer association, not independently verified')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
