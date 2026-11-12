"""Browser check that a grounded-looking model rewrite still shows review warnings."""
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
class TailoringWarningsBrowserTests(unittest.TestCase):
    def test_unverified_added_metric_is_visible_as_a_warning(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        from test_dashboard import fake_fact_bank
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        job = Job.from_mapping({'title': 'Data Analyst', 'company': 'Fictional Company',
            'job_url': 'https://example.invalid/jobs/fictional', 'description': 'Python skills.'})
        output = {'suggestions': [{'fact_id': 'experience-01', 'job_quote': 'Python skills.',
            'wording': 'Built a fictional program that cut costs by 40%.'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'; facts = root / 'fictional.json'
            facts.write_text(json.dumps(fake_fact_bank()), encoding='utf-8')
            with Store(db) as store: store.add_jobs([job])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', facts_path=facts))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-ai').click(); expect(page.locator('#ai-tailor')).to_be_enabled()
                        page.locator('#ai-consent').check(); page.locator('#ai-tailor').click()
                        expect(page.locator('#ai-results')).to_contain_text('Review warning: New numeric or percentage text')
                        expect(page.locator('#ai-status')).to_contain_text('absence of warnings do not prove')
                        self.assertFalse((root / 'packets').exists())
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
