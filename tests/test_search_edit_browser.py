"""Fictional paused-search editing without external discovery requests."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class SearchEditBrowserTests(unittest.TestCase):
    def test_paused_search_edit_is_preserved_and_does_not_start_discovery(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.automation import Automation
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            auto = Automation(db)
            sid = auto.save({'query': 'Fictional engineer', 'location': 'Remote', 'sites': ['indeed'],
                             'interval_minutes': 360, 'results': 10, 'acknowledged': True})['id']
            auto.enable(sid, False)
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.automation.fetch') as fetch, sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-automation').click()
                        card = page.locator('#automation-searches section').first
                        expect(card).to_contain_text('paused')
                        card.get_by_text('Edit paused search').click()
                        card.get_by_label('Job titles / query').fill('Fictional data engineer')
                        card.get_by_label('Indeed').uncheck()
                        card.get_by_label('LinkedIn').check()
                        card.get_by_label('Every (minutes)').fill('720')
                        page.locator('#automation-refresh').click()
                        expect(card.get_by_label('Job titles / query')).to_have_value('Fictional data engineer')
                        card.get_by_label('I confirm these settings for future recurring searches.').check()
                        card.get_by_role('button', name='Save paused search').click()
                        expect(page.locator('#automation-searches')).to_contain_text('Fictional data engineer')
                        expect(page.locator('#automation-searches')).to_contain_text('linkedin')
                        page.reload(); page.locator('#nav-automation').click()
                        expect(page.locator('#automation-searches')).to_contain_text('Fictional data engineer')
                        row = auto.list()['schedules'][0]
                        self.assertEqual(row['id'], sid)
                        self.assertFalse(row['enabled'])
                        self.assertEqual(row['settings']['sites'], ['linkedin'])
                        self.assertEqual(row['interval_minutes'], 720)
                        fetch.assert_not_called()
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
