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
class LocalModelsBrowserTests(unittest.TestCase):
    def test_local_inventory_is_explicit_and_treats_ids_as_text(self):
        import httpx
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        routes = {'local': {'provider': 'ollama', 'model': 'alias', 'base_url': 'http://127.0.0.1:11434/v1'}}
        requests = []
        class Stream(httpx.SyncByteStream):
            def __iter__(self): yield json.dumps({'data': [{'id': '<script>demo</script>'}]}).encode()
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={'content-type': 'application/json'}, stream=Stream())
        def client(): return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'test.sqlite3'
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.ai.routes', return_value=routes), patch('career_agent.ai._inventory_transport', side_effect=client), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-ai').click()
                        expect(page.locator('#ai-local-models')).to_be_enabled()
                        self.assertEqual(requests, [])
                        page.get_by_text('Check local model IDs', exact=True).first.click()
                        page.locator('#ai-local-models').click()
                        expect(page.locator('#ai-local-models-results')).to_contain_text('<script>demo</script>')
                        self.assertEqual(page.locator('#ai-local-models-results script').count(), 0)
                        expect(page.locator('#ai-local-models-status')).to_contain_text('aliases may work')
                        self.assertEqual(len(requests), 1)
                        with Store(db) as store:
                            self.assertEqual(store.list_jobs(), [])
                            self.assertEqual(CandidateWorkspace(store).latest()['revision'], 0)
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
