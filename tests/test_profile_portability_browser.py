"""Fictional profile export and import, with verification reset."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class ProfilePortabilityBrowserTests(unittest.TestCase):
    def test_export_and_import_into_a_separate_workspace(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        from test_workspace import preferences, fact
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source_db = root / 'source.sqlite3'; target_db = root / 'target.sqlite3'
            with Store(source_db) as store:
                workspace = CandidateWorkspace(store)
                saved = workspace.save(0, preferences(), [fact()])
                workspace.verify(saved['revision'], 'pilot-01', True, True)
            servers = []
            try:
                for db, name in ((source_db, 'source'), (target_db, 'target')):
                    app = create_app(DashboardConfig(db_path=db, packet_dir=root / (name + '-packets')))
                    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
                    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
                    servers.append((server, thread, sock, port))
                    for _ in range(100):
                        if server.started: break
                        time.sleep(.05)
                    self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{servers[0][3]}')
                        page.locator('#nav-profile').click()
                        expect(page.locator('#profile-screen')).to_have_attribute('aria-busy', 'false')
                        page.get_by_text('Export or import career profile').click()
                        with page.expect_download() as download:
                            page.get_by_role('button', name='Download career profile JSON').click()
                        data = Path(download.value.path()).read_bytes()
                        exported = json.loads(data)
                        self.assertTrue(exported['facts'][0]['verified_by_candidate'])
                        self.assertEqual(exported['facts'][0]['source_note'], 'Private fictional project note')
                        page.goto(f'http://127.0.0.1:{servers[1][3]}')
                        page.locator('#nav-profile').click()
                        expect(page.locator('#profile-screen')).to_have_attribute('aria-busy', 'false')
                        page.get_by_text('Export or import career profile').click()
                        page.locator('#career-import-file').set_input_files({'name': 'profile.json', 'mimeType': 'application/json', 'buffer': data})
                        page.locator('#career-name').fill('Unsaved fictional name')
                        page.once('dialog', lambda dialog: dialog.dismiss())
                        page.get_by_role('button', name='Import profile for review').click()
                        expect(page.locator('#career-name')).to_have_value('Unsaved fictional name')
                        with Store(target_db) as store:
                            self.assertEqual(CandidateWorkspace(store).latest()['revision'], 0)
                        page.once('dialog', lambda dialog: dialog.accept())
                        page.get_by_role('button', name='Import profile for review').click()
                        expect(page.locator('#career-portable-status')).to_contain_text('Profile imported locally')
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        expect(page.locator('#career-facts-list')).to_contain_text('Private fictional project note')
                        with Store(target_db) as store:
                            imported = CandidateWorkspace(store).latest()
                            self.assertFalse(imported['facts'][0]['verified_by_candidate'])
                        page.reload(); page.locator('#nav-profile').click()
                        expect(page.locator('#career-facts-list')).to_contain_text('Not verified')
                        expect(page.locator('#career-portable-import')).to_be_hidden()
                    finally: browser.close()
            finally:
                for server, thread, sock, _port in servers:
                    server.should_exit = True; thread.join(timeout=10); sock.close()
