"""Optional real Chromium coverage of H-1B configuration, import and research."""
import os
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_h1b import lca_csv
from career_agent.models import Job
from career_agent.storage import Store


@unittest.skipUnless(os.environ.get('CAREER_AGENT_TEST_BROWSER') == '1', 'Enable with installed Chromium')
class H1BBrowserTests(unittest.TestCase):
    def test_board_search_finds_job_beyond_500_with_real_api(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Fictional Co',
                    'title': 'Rare oldest role' if i == 0 else f'Role {i:03}',
                    'job_url': f'https://example.invalid/role/{i}'}) for i in range(505)])
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
                        expect(page.locator('#visible-count')).to_have_text('505 opportunities', timeout=30000)
                        page.locator('#search').fill('Rare oldest role')
                        expect(page.locator('#visible-count')).to_have_text('1 opportunity')
                        expect(page.locator('#job-list')).to_contain_text('Rare oldest role')
                        page.locator('#nav-ai').click()
                        expect(page.locator('#ai-job option')).to_have_count(505, timeout=30000)
                        expect(page.locator('#ai-job')).to_contain_text('Rare oldest role')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_open_views_age_posting_observations_without_reloading(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'test.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': 'Role',
                    'job_url': 'https://boards.greenhouse.io/fictional/jobs/123'})])
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                scenario = {'checked_at': None}
                def almost_stale(_id):
                    return {'availability': 'listed', 'reason': 'Fictional check', 'source_url': 'https://example.invalid/role',
                            'checked_at': scenario['checked_at'] or (datetime.now(timezone.utc) - timedelta(seconds=86398)).isoformat(), 'stale': False}
                with patch('career_agent.posting_checks.PostingChecks.latest', side_effect=almost_stale), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        expect(page.locator('#job-list')).to_contain_text('Listed at check')
                        page.get_by_role('button', name='View Role at Fictional Co').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Listed at check')
                        page.route('**/api/jobs/*/live-check', lambda route: route.fulfill(
                            status=502, content_type='application/json', body='{"detail":"Fictional check failed"}'))
                        page.get_by_role('button', name='Check live availability').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional check failed')
                        expect(page.locator('#job-list')).to_contain_text('Check is stale', timeout=6000)
                        expect(page.locator('#detail-panel')).to_contain_text('Check is stale')
                        expect(page.locator('#detail-panel')).to_contain_text('Fictional check failed')
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        result = page.locator('#h1b-opportunities-results')
                        expect(result).to_contain_text('listed')
                        expect(result).to_contain_text('STALE OBSERVATION', timeout=6000)
                        expect(result).to_contain_text('unknown')
                        for invalid in ('not-a-date', (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
                            scenario['checked_at'] = invalid
                            page.get_by_role('button', name='H-1B research', exact=True).click()
                            expect(result).to_contain_text(invalid)
                            expect(result).to_contain_text('STALE OBSERVATION')
                            expect(result).to_contain_text('unknown')
                            expect(result).not_to_contain_text('listed')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_navigation_pagination_and_contextual_ai_prerequisite(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Fictional Co', 'title': f'Role {i:02}',
                    'job_url': f'https://example.invalid/jobs/{i}', 'description': 'Python skills.'}) for i in range(12)])
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
                        expect(page.locator('#profile-prompt')).to_be_visible()
                        expect(page.locator('#sort-label')).to_have_text('Recently added')
                        page.locator('#search').fill('no fictional match')
                        expect(page.locator('#job-list')).to_contain_text('No opportunities match these filters')
                        page.get_by_role('button', name='Clear filters', exact=True).click()
                        expect(page.locator('#search')).to_have_value('')
                        expect(page.locator('#jobs-page')).to_have_text('Page 1 of 2')
                        self.assertEqual(page.locator('#job-list button').count(), 10)
                        page.locator('#jobs-next').click()
                        expect(page.locator('#jobs-page')).to_have_text('Page 2 of 2')
                        self.assertEqual(page.locator('#job-list button').count(), 2)
                        page.locator('#job-list button').first.click()
                        selected = page.locator('#job-list button').first.get_attribute('aria-label')
                        with patch('career_agent.ai.routes', return_value={'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}):
                            page.get_by_role('button', name='Analyze with AI').click()
                            expect(page.locator('#ai-screen')).to_be_visible()
                            expect(page.locator('#ai-status')).to_contain_text('Verify at least one career fact')
                            expect(page.locator('#ai-run')).to_be_disabled()
                            self.assertIn(page.locator('#ai-job option:checked').inner_text().split(' — ')[1], selected)
                            page.locator('#ai-open-profile').click()
                            expect(page.locator('#profile-screen')).to_be_visible()
                            expect(page.locator('#ai-screen')).to_be_hidden()
                            expect(page.locator('#automation-screen')).to_be_hidden()
                            page.get_by_text('Import and review résumé text', exact=True).click()
                            page.locator('#resume-file').set_input_files({
                                'name': 'fictional.txt', 'mimeType': 'text/plain',
                                'buffer': b'Fictional data analysis experience.'})
                            page.locator('#resume-file-extract').click()
                            expect(page.locator('#resume-text')).to_have_value('Fictional data analysis experience.')
                            page.locator('#resume-extract').click()
                            page.locator('#resume-use-line').click()
                            expect(page.locator('#fact-statement')).to_have_value('Fictional data analysis experience.')
                            expect(page.locator('#career-facts-list')).to_contain_text('No career facts yet')
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_import_preferences_search_and_safe_rendering(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        from unittest.mock import patch
        from test_uscis import hub_csv, URL

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.sqlite3'
            with Store(db) as store:
                store.add_jobs([Job.from_mapping({'company': 'Example Inc.', 'title': 'Transfer role',
                    'job_url': 'https://example.invalid/transfer', 'description': 'H-1B transfers only.'}),
                    Job.from_mapping({'company': 'Example Inc.', 'title': 'Unknown role',
                    'job_url': 'https://example.invalid/unknown', 'description': 'Python skills.'})])
            from test_dashboard import fake_fact_bank
            facts = root / 'facts.json'
            facts.write_text(json.dumps(fake_fact_bank()))
            app = create_app(DashboardConfig(db_path=db, packet_dir=root / 'packets', facts_path=facts))
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{port}')
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-coverage')).to_contain_text('0 imported')
                        expect(page.locator('#h1b-opportunities-status')).to_contain_text('2 matching opportunities')
                        expect(page.locator('#h1b-opportunities-results')).to_contain_text('Transfer role')
                        page.locator('#h1b-opportunities-category').select_option('review')
                        page.get_by_role('button', name='Find opportunities', exact=True).click()
                        expect(page.locator('#h1b-opportunities-results')).to_contain_text('Unknown role')
                        expect(page.locator('#h1b-opportunities-results')).not_to_contain_text('Transfer role')
                        page.get_by_role('button', name='Find more jobs with JobSpy').click()
                        expect(page.locator('#discovery-panel')).to_have_attribute('open', '')
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        page.locator('#h1b-opportunities-category').select_option('all')
                        page.get_by_role('button', name='Find opportunities', exact=True).click()
                        expect(page.locator('#h1b-opportunities-status')).to_contain_text('2 matching opportunities')
                        page.locator('#h1b-need').select_option('new')
                        page.locator('#h1b-min-year').fill('2024')
                        page.locator('#h1b-unknown').uncheck()
                        page.get_by_role('button', name='Save H-1B preferences').click()
                        expect(page.locator('#h1b-status')).to_contain_text('preferences saved')
                        page.get_by_text('Import public DOL LCA records', exact=True).click()
                        page.locator('#h1b-captured').fill('2025-06-01')
                        page.locator('#h1b-file').set_input_files({'name': 'fictional.csv', 'mimeType': 'text/csv',
                            'buffer': lca_csv([{'JOB_TITLE': '<script>throw new Error("unsafe")</script>'}]).encode()})
                        page.locator('#h1b-consent').check()
                        page.get_by_role('button', name='Import records locally', exact=True).click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 added')
                        page.get_by_text('USCIS employer decisions: refresh or import', exact=True).click()
                        page.locator('#uscis-source').fill(URL)
                        page.locator('#uscis-year').fill('2025')
                        page.locator('#uscis-as-of').fill('2025-09-30')
                        page.locator('#uscis-file').set_input_files({'name': 'uscis-fictional.csv', 'mimeType': 'text/csv',
                            'buffer': hub_csv([{'Employer': 'Example Inc.'}])})
                        page.locator('#uscis-consent').check()
                        page.get_by_role('button', name='Import official CSV locally').click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 employer groups')
                        with patch('career_agent.uscis.public_get', return_value=(403, b'', 'text/html')):
                            page.locator('#uscis-consent').check()
                            page.get_by_role('button', name='Refresh from USCIS').click()
                            expect(page.locator('#h1b-status')).to_contain_text('HTTP 403')
                            expect(page.locator('#h1b-status')).to_contain_text('No USCIS API token is needed')
                        page.route('**/api/h1b/uscis/refresh', lambda route: route.fulfill(
                            status=403, content_type='application/json', body='{"detail":"Session token required"}'))
                        page.get_by_role('button', name='Refresh from USCIS').click()
                        expect(page.locator('#h1b-status')).to_contain_text('refresh this page')
                        expect(page.locator('#h1b-status')).to_contain_text('no USCIS token is required')
                        page.unroute('**/api/h1b/uscis/refresh')
                        page.locator('#h1b-query').fill('Example')
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 stored jobs')
                        expect(page.locator('#h1b-results')).to_contain_text('does not meet a new-sponsorship preference')
                        expect(page.locator('#h1b-results')).to_contain_text('<script>throw new Error')
                        expect(page.locator('#h1b-results')).to_contain_text('initial approvals/denials: 10/1')
                        expect(page.locator('#h1b-results').get_by_role('button', name='Check live ATS posting')).to_be_disabled()
                        expect(page.locator('#h1b-results')).to_contain_text('Check this original posting manually')
                        self.assertEqual(page.locator('#h1b-results script').count(), 0)
                        page.locator('#h1b-query').fill('Unmatched Company')
                        expect(page.locator('#h1b-results')).to_be_empty()
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-results')).to_contain_text('No employer records found')
                        page.reload()
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-need')).to_have_value('new')
                        expect(page.locator('#h1b-unknown')).not_to_be_checked()
                        with patch('career_agent.ai.routes', return_value={'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}), patch('career_agent.ai.complete', return_value=json.dumps({'requirements': [{'quote': 'Python skills.', 'fact_ids': ['experience-01'], 'reason': '<script>unsafe()</script>'}]})) as complete:
                            page.get_by_role('button', name='AI analysis', exact=True).click()
                            expect(page.locator('#ai-run')).to_be_enabled()
                            options = page.locator('#ai-job option').all()
                            selected = next(option.get_attribute('value') for option in options if 'Unknown role' in option.inner_text())
                            page.locator('#ai-job').select_option(selected)
                            page.locator('#ai-run').click()
                            complete.assert_not_called()
                            page.locator('#ai-consent').check()
                            page.locator('#ai-run').click()
                            expect(page.locator('#ai-results')).to_contain_text('<script>unsafe()</script>')
                            self.assertEqual(page.locator('#ai-results script').count(), 0)
                            expect(page.locator('#ai-consent')).not_to_be_checked()
                            self.assertNotIn('Private fictional evidence', complete.call_args.args[1])
                            page.get_by_role('button', name='Application tracker', exact=True).click()
                            page.get_by_role('button', name='AI analysis', exact=True).click()
                            expect(page.locator('#ai-run')).to_be_enabled()
                            expect(page.locator('#ai-route')).to_have_value('local')
                            expect(page.locator('#ai-job')).to_have_value(selected)
                            expect(page.locator('#ai-results')).to_contain_text('<script>unsafe()</script>')
                            self.assertEqual(complete.call_count, 1)
                            # A delayed result for another job must not appear under the newly selected role.
                            page.get_by_role('button', name='Opportunity board', exact=True).click()
                            page.get_by_role('button', name='View Transfer role at Example Inc.').click()
                            page.get_by_role('button', name='Analyze with AI').click()
                            expect(page.locator('#ai-run')).to_be_enabled()
                            expect(page.locator('#ai-results')).to_be_empty()
                            started, release = threading.Event(), threading.Event()
                            def delayed(_route, _prompt):
                                started.set()
                                if not release.wait(5): raise RuntimeError('Timed out waiting for test')
                                return '{"requirements":[]}'
                            complete.side_effect = delayed
                            try:
                                page.locator('#ai-consent').check()
                                page.locator('#ai-run').click()
                                self.assertTrue(started.wait(5))
                                page.get_by_role('button', name='Opportunity board', exact=True).click()
                                page.get_by_role('button', name='View Unknown role at Example Inc.').click()
                                page.get_by_role('button', name='Analyze with AI').click()
                            finally:
                                release.set()
                            expect(page.locator('#ai-run')).to_be_enabled()
                            expect(page.locator('#ai-job')).to_have_value(selected)
                            expect(page.locator('#ai-results')).to_be_empty()
                            complete.side_effect = None
                            with patch('career_agent.ai.complete', side_effect=ValueError('Provider unavailable')):
                                page.locator('#ai-consent').check()
                                page.locator('#ai-run').click()
                                expect(page.locator('#ai-status')).to_contain_text('Provider unavailable')
                                expect(page.locator('#ai-results')).to_be_empty()
                        with patch('career_agent.automation.fetch', return_value=[]):
                            page.get_by_role('button', name='Saved searches', exact=True).click()
                            page.locator('#schedule-query').fill('Fictional AI lead')
                            page.locator('#schedule-consent').check()
                            page.locator('#schedule-save').click()
                            expect(page.locator('#automation-searches')).to_contain_text('Fictional AI lead')
                            page.locator('#automation-searches').get_by_role('button', name='Pause', exact=True).click()
                            expect(page.locator('#automation-searches')).to_contain_text('paused')
                            page.reload()
                            page.get_by_role('button', name='Saved searches', exact=True).click()
                            expect(page.locator('#automation-searches')).to_contain_text('paused')
                            page.get_by_role('button', name='Remove saved search', exact=True).click()
                            expect(page.locator('#automation-searches')).to_be_empty()
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
