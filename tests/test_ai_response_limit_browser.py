"""Browser-to-provider error handling with fictional data only."""
import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1' and importlib.util.find_spec('openai'), 'Enable with Chromium and AI extras')
class AIResponseLimitBrowserTests(unittest.TestCase):
    def test_oversized_response_fails_closed_and_records_only_metadata(self):
        import httpx
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent import ai
        from career_agent.ai_history import AIHistory
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.workspace import CandidateWorkspace
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1', 'daily_request_limit': 2}}
        calls = []
        mode = {'refusal': False}
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for _ in range(4): yield b'x' * (400 * 1024)
        def handler(request):
            calls.append(request)
            if mode['refusal']:
                return httpx.Response(200, json={'id': 'fictional', 'object': 'chat.completion', 'created': 0,
                    'model': 'fictional', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': 'partial fictional text', 'refusal': 'private refusal'}}]})
            return httpx.Response(200, headers={'content-type': 'application/json'}, stream=Stream())
        def client(): return httpx.Client(transport=ai._bounded_transport(httpx.MockTransport(handler)), trust_env=False)
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
                with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai._transport', side_effect=client), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-ai').click()
                        expect(page.locator('#ai-test-route')).to_be_enabled()
                        page.locator('#ai-test-route').click()
                        expect(page.locator('#ai-test-status')).to_contain_text('confirm the test request')
                        self.assertEqual(calls, [])
                        page.locator('#ai-test-consent').check(); page.locator('#ai-test-route').click()
                        expect(page.locator('#ai-test-status')).to_contain_text('response exceeded the local size limit')
                        expect(page.locator('#ai-test-consent')).not_to_be_checked()
                        self.assertEqual(len(calls), 1)
                        payload = json.loads(calls[0].content)
                        self.assertEqual(payload['messages'][-1]['content'], 'Reply with OK.')
                        with Store(db) as store:
                            history = AIHistory(store).recent()
                            self.assertEqual(history[0]['status'], 'failed')
                            self.assertNotIn('Reply with OK', json.dumps(history))
                            self.assertEqual(CandidateWorkspace(store).latest()['revision'], 0)
                        mode['refusal'] = True
                        page.locator('#ai-test-consent').check(); page.locator('#ai-test-route').click()
                        expect(page.locator('#ai-test-status')).to_contain_text('refused, or contained unsupported content')
                        expect(page.locator('#ai-test-consent')).not_to_be_checked()
                        self.assertEqual(len(calls), 2)
                        with Store(db) as store:
                            history = AIHistory(store).recent()
                            self.assertEqual([row['status'] for row in history], ['failed', 'failed'])
                            self.assertNotIn('private', json.dumps(history))
                            self.assertEqual(CandidateWorkspace(store).latest()['revision'], 0)
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
