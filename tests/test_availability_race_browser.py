"""An async packet redraw must not discard a live-check result."""
import os
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone

@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class AvailabilityRaceBrowserTests(unittest.TestCase):
    def test_failed_check_survives_delayed_packet_redraw(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': 'Role',
                    'job_url': 'https://boards.greenhouse.io/fictional/jobs/123', 'description': 'Fictional role.'}),
                    Job.from_mapping({'company': 'Other Co', 'title': 'Other role',
                    'job_url': 'https://boards.greenhouse.io/fictional/jobs/456', 'description': 'Another fictional role.'})])
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
                              if (String(url).endsWith('/packet')) {
                                return new Promise((resolve, reject) => {
                                  window.releasePacket = () => realFetch(url, options).then(resolve, reject);
                                });
                              }
                              if (String(url).endsWith('/live-check') && window.holdCheck) {
                                return new Promise((resolve, reject) => {
                                  window.releaseCheck = () => realFetch(url, options).then(resolve, reject);
                                });
                              }
                              return realFetch(url, options);
                            };
                        ''')
                        page.route('**/api/jobs/*/live-check', lambda route: route.fulfill(
                            status=502, content_type='application/json', body='{"detail":"Fictional check failed"}'))
                        page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='View Role at Fictional Co').click()
                        page.get_by_role('button', name='Check live availability').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional check failed')
                        heading = page.locator('#detail-panel h3').element_handle()
                        page.wait_for_function('typeof window.releasePacket === "function"')
                        page.evaluate('window.releasePacket()')
                        page.wait_for_function('(element) => !element.isConnected', arg=heading)
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional check failed')
                        page.evaluate('window.holdCheck = true')
                        page.get_by_role('button', name='Check live availability').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Checking the public posting')
                        page.get_by_role('button', name='View Role at Fictional Co').click()
                        expect(page.get_by_role('button', name='Check live availability')).to_be_disabled()
                        expect(page.locator('#detail-panel')).to_contain_text('Checking the public posting')
                        page.wait_for_function('typeof window.releaseCheck === "function"')
                        page.evaluate('window.releaseCheck()')
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional check failed')
                        expect(page.get_by_role('button', name='Check live availability')).to_be_enabled()
                        page.evaluate('window.releaseCheck = null')
                        page.get_by_role('button', name='Check live availability').click()
                        page.wait_for_function('typeof window.releaseCheck === "function"')
                        page.get_by_role('button', name='View Other role at Other Co').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Other role')
                        page.get_by_role('button', name='View Role at Fictional Co').click()
                        expect(page.get_by_role('button', name='Check live availability')).to_be_disabled()
                        page.get_by_role('button', name='View Other role at Other Co').click()
                        with page.expect_response(lambda response: response.url.endswith('/live-check')):
                            page.evaluate('window.releaseCheck()')
                        expect(page.locator('#detail-panel')).not_to_contain_text('Fictional check failed')
                        page.evaluate('window.holdCheck = false')
                        page.route('**/api/jobs/*/live-check', lambda route: route.fulfill(status=200,
                            content_type='application/json', body=json.dumps({'availability': 'listed',
                            'reason': 'Fictional fresh check', 'source_url': 'https://example.invalid/source',
                            'checked_at': datetime.now(timezone.utc).isoformat(), 'stale': False})))
                        page.get_by_role('button', name='View Role at Fictional Co').click()
                        page.get_by_role('button', name='Check live availability').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional fresh check')
                        heading = page.locator('#detail-panel h3').element_handle()
                        page.evaluate('window.releasePacket()')
                        page.wait_for_function('(element) => !element.isConnected', arg=heading)
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional fresh check')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
