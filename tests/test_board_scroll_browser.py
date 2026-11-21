"""Regression: choosing a role from a long list must not create endless page scrolling."""
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class BoardScrollTests(unittest.TestCase):
    def test_long_lists_scroll_inside_their_panes_with_detail_in_view(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            jobs = [Job(f'Fictional Co {i}', f'Fictional Analyst {i}', 'Boston', f'https://example.invalid/role/{i}',
                        'Requirements:\n- Python\n' + 'Fictional duties. ' * 40) for i in range(60)]
            with Store(db) as store:
                store.add_jobs(jobs)
                tracked_ids = [job.id for job in store.list_jobs(limit=-1)][:40]
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 900})
                        page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_role('button', name='Opportunity board').click()
                        page.locator('#job-list .job-item').nth(4).click()
                        expect(page.get_by_role('tab', name='Overview')).to_have_attribute('aria-selected', 'true')
                        # Triage signals stay visible on cards; the rest is detail-pane content.
                        expect(page.locator('#job-list .job-item').first.locator('.board-decision-field:not(.board-decision-secondary)').first).to_be_visible()
                        expect(page.locator('#job-list .board-decision-secondary').first).not_to_be_in_viewport()
                        scroll = page.evaluate('''() => {
                            const list = document.querySelector('#job-list');
                            return {viewport: innerHeight, listScrolls: list.scrollHeight > list.clientHeight,
                                    listPanel: document.querySelector('#jobs-screen .list-panel').getBoundingClientRect().height,
                                    detailOverflow: getComputedStyle(document.querySelector('#detail-panel')).overflowY};
                        }''')
                        # The list is a bounded, pinned navigator; the detail flows with the single page scroll.
                        self.assertTrue(scroll['listScrolls'])
                        self.assertLessEqual(scroll['listPanel'], scroll['viewport'])
                        self.assertEqual(scroll['detailOverflow'], 'visible')

                        # Only roles you acted on appear in the tracker, and its list is bounded too.
                        page.get_by_role('button', name='Application tracker').click()
                        expect(page.locator('#crm-result-count')).to_have_text('0 of 0 tracked roles')
                        page.get_by_role('button', name='Opportunity board').click()
                        page.get_by_role('button', name='Add to tracker').click()
                        expect(page.get_by_role('button', name='In tracker · saved')).to_be_visible()
                        page.get_by_role('button', name='Application tracker').click()
                        expect(page.locator('#crm-result-count')).to_have_text('1 of 1 tracked roles')
                        page.evaluate('''async (ids) => { const t = document.getElementById('app').dataset.session;
                            for (const id of ids) await fetch('/api/jobs/' + id + '/crm/stage', {method: 'POST', body: '{"stage":"saved","note":""}',
                                headers: {'X-Session-Token': t, 'Content-Type': 'application/json'}}); }''', tracked_ids)
                        page.get_by_role('button', name='Opportunity board').click()
                        page.get_by_role('button', name='Application tracker').click()
                        expect(page.locator('#crm-result-count')).to_have_text(re.compile(r'^4[01] of 4[01] tracked roles$'))
                        page.locator('#crm-jobs button').nth(30).click(force=True)
                        expect(page.locator('#crm-detail')).to_contain_text('Fictional Analyst')
                        tracker = page.evaluate('''() => { const jobs = document.querySelector('#crm-jobs');
                            return {listScrolls: jobs.scrollHeight > jobs.clientHeight, viewport: innerHeight,
                                    panel: document.querySelector('#tracker-screen .list-panel').getBoundingClientRect().height,
                                    detailOverflow: getComputedStyle(document.querySelector('#crm-detail')).overflowY}; }''')
                        self.assertTrue(tracker['listScrolls'])
                        self.assertLessEqual(tracker['panel'], tracker['viewport'])
                        self.assertEqual(tracker['detailOverflow'], 'visible')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
