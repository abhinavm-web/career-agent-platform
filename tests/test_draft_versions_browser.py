"""Compare exact fictional workflow drafts without altering approval."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path

@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class DraftVersionsBrowserTests(unittest.TestCase):
    def test_previous_draft_compares_with_current_without_approval(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.storage import Store
        from career_agent.models import CandidateProfile
        from career_agent.candidate import FactBank
        from career_agent.workflows import Workflows, inputs, signature
        from test_dashboard import fake_fact_bank, fake_job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'; packets = root / 'packets'
            job = fake_job(); profile = CandidateProfile(('AI Program Lead',), ('Python',), (), True)
            old = fake_fact_bank(); new = fake_fact_bank()
            new['facts'][0]['statement'] = 'A changed fictional verified statement.'
            with Store(db) as store: store.add_jobs([job])
            manager = Workflows(db, packets)
            old_state = inputs(job, profile, FactBank.parse(old), ())
            first = manager.start(old_state)
            manager.advance(first['run_id'], signature(old_state), {
                'approved': True, 'reviewed': True, 'packet_sha256': first['packet']['packet_sha256']})
            latest = manager.start(inputs(job, profile, FactBank.parse(new), ()))
            profile_path, facts_path = root / 'profile.json', root / 'facts.json'
            profile_path.write_text(json.dumps(asdict(profile))); facts_path.write_text(json.dumps(new))
            app = create_app(DashboardConfig(db_path=db, packet_dir=packets, profile_path=profile_path, facts_path=facts_path))
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
                        page.locator('#nav-jobs').click()
                        page.get_by_role('button', name='View AI Program Lead at Example Company').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Application packet')
                        page.get_by_text('Compare previous draft versions', exact=True).click()
                        page.get_by_role('button', name='Load draft versions').click()
                        expect(page.get_by_label('Previous workflow draft')).to_have_value(first['run_id'])
                        page.get_by_role('button', name='Compare with current draft').click()
                        comparison = page.locator('.resume-comparison-grid').filter(has_text='Selected stored draft')
                        expect(comparison).to_contain_text('Delivered a fictional AI program')
                        expect(comparison).to_contain_text('A changed fictional verified statement.')
                        expect(comparison).not_to_contain_text('Private fictional evidence')
                        with Store(db) as store:
                            self.assertEqual(store.latest_review(job.id, first['packet']['packet_sha256'])['decision'], 'approved_for_manual_submission')
                            self.assertIsNone(store.latest_review(job.id, latest['packet']['packet_sha256']))
                        self.assertEqual(manager.list()[0]['packet_sha256'], latest['packet']['packet_sha256'])
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()
