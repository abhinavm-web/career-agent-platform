"""Fact actions expose their busy state while a profile write is pending."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ProfileBusyBrowserTests(unittest.TestCase):
    def test_fact_actions_are_disabled_until_profile_save_finishes(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from career_agent.models import CandidateProfile
        from career_agent.candidate import FactBank
        from test_dashboard import fake_fact_bank
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            with Store(db) as store:
                workspace = CandidateWorkspace(store)
                saved = workspace.import_legacy(CandidateProfile(('Analyst',), ('Python',), (), True), FactBank.parse(fake_fact_bank()))
                workspace.verify(saved['revision'], 'experience-01', False, True)
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
                        page = browser.new_page()
                        page.add_init_script('''
                            const realFetch = window.fetch.bind(window);
                            window.fetch = (url, options) => {
                              if (String(url) === '/api/workspace' && options?.method === 'PUT' && !window.saveHeld) {
                                window.saveHeld = true;
                                return new Promise((resolve, reject) => {
                                  window.releaseSave = () => realFetch(url, options).then(resolve, reject);
                                });
                              }
                              return realFetch(url, options);
                            };
                        ''')
                        page.goto(f'http://127.0.0.1:{port}'); page.locator('#nav-profile').click()
                        expect(page.get_by_role('button', name='Verify this fact')).to_be_enabled()
                        page.locator('#career-headline').fill('Updated fictional headline')
                        page.get_by_role('button', name='Save preferences').click()
                        page.wait_for_function('typeof window.releaseSave === "function"')
                        expect(page.locator('#profile-screen')).to_have_attribute('aria-busy', 'true')
                        expect(page.get_by_role('button', name='Verify this fact')).to_be_disabled()
                        page.evaluate('window.releaseSave()')
                        expect(page.locator('#profile-screen')).to_have_attribute('aria-busy', 'false')
                        expect(page.get_by_role('button', name='Verify this fact')).to_be_enabled()
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Verify this fact').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Candidate verified')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
