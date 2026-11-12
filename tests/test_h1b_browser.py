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
    def test_empty_history_explains_missing_import_then_shows_local_evidence(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'fictional.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Employer', 'title': 'Engineer',
                                    'job_url': 'https://example.invalid/role', 'description': ''})
            with Store(db) as store: store.add_jobs([job])
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
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-h1b').click()
                        page.locator('#h1b-query').fill('Fictional Employer')
                        page.get_by_role('button', name='Search company records and jobs').click()
                        results = page.locator('#h1b-results')
                        expect(results).to_contain_text('No DOL LCA or USCIS records have been imported')
                        expect(results).to_contain_text('No DOL LCA records have been imported')
                        expect(results).not_to_contain_text('Recent certified LCA cases')
                        expect(results.get_by_role('link', name='Go to data administration')).to_have_attribute('href', '#h1b-data-admin-heading')
                        page.get_by_text('Import public DOL LCA records', exact=True).click()
                        page.locator('#h1b-captured').fill('2025-06-01')
                        page.locator('#h1b-file').set_input_files({'name': 'fictional.csv', 'mimeType': 'text/csv',
                            'buffer': lca_csv([{'EMPLOYER_NAME': 'Fictional Employer'}]).encode()})
                        page.locator('#h1b-consent').check()
                        page.get_by_role('button', name='Import records locally', exact=True).click()
                        expect(page.locator('#h1b-import-status')).to_contain_text('1 added')
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(results).to_contain_text('Certified LCA cases in imported data since FY')
                        expect(results).to_contain_text('No USCIS snapshots have been imported')
                        expect(results).not_to_contain_text('No DOL LCA or USCIS records have been imported')
                        with Store(db) as store:
                            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM h1b_lca').fetchone()[0], 1)
                        page.locator('#nav-jobs').click(); page.locator('#refresh').click()
                        page.locator('#filter').select_option('imported-history')
                        expect(page.locator('#job-list')).to_contain_text('Imported employer history')
                        page.locator('#job-list button').first.click()
                        expect(page.locator('#detail-panel')).to_contain_text('DOL LCA · employer name: Fictional Employer')
                        expect(page.locator('#detail-panel')).to_contain_text('do not establish current job sponsorship')
                    finally: browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_dol_progress_is_local_visible_and_announced_by_phase(self):
        import uvicorn
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app
        advance, applying, finish = threading.Event(), threading.Event(), threading.Event()
        def fake_refresh(_research, _url, *, progress):
            progress('reading', 2000); advance.wait(15)
            progress('reading', 4000); applying.wait(15)
            progress('applying', 0); finish.wait(15)
            return {'added': 1, 'updated': 0, 'duplicates': 0, 'older_skipped': 0, 'other_visas_skipped': 0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app(DashboardConfig(db_path=root / 'fictional.sqlite3', packet_dir=root / 'packets'))
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with patch('career_agent.h1b.H1BResearch.refresh_dol', new=fake_refresh), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        page.locator('#nav-h1b').click()
                        page.get_by_text('Import public DOL LCA records', exact=True).click()
                        page.locator('#h1b-refresh-url').fill('https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx')
                        page.locator('#h1b-refresh-consent').check()
                        page.locator('#h1b-refresh-submit').click()
                        expect(page.locator('#h1b-refresh-status')).to_be_focused()
                        expect(page.locator('#h1b-refresh-status')).to_contain_text('2,000', timeout=8000)
                        expect(page.locator('#h1b-refresh-announcement')).to_contain_text('2,000')
                        observer = browser.new_page(); observer.goto(f'http://127.0.0.1:{port}')
                        observer.locator('#nav-h1b').click()
                        expect(observer.locator('#h1b-active-import')).to_contain_text('validating workbook rows')
                        expect(observer.locator('#h1b-active-import')).to_contain_text('not a completed import')
                        advance.set()
                        expect(page.locator('#h1b-refresh-status')).to_contain_text('4,000', timeout=8000)
                        expect(page.locator('#h1b-refresh-announcement')).to_contain_text('2,000')
                        applying.set()
                        expect(page.locator('#h1b-refresh-announcement')).to_contain_text('Applying', timeout=8000)
                        finish.set()
                        expect(page.locator('#h1b-refresh-status')).to_contain_text('1 added')
                        expect(page.locator('#h1b-refresh-announcement')).to_contain_text('1 added')
                        expect(observer.locator('#h1b-active-import')).to_contain_text('outcome is unknown', timeout=8000)
                        expect(observer.locator('#h1b-active-import')).not_to_contain_text('1 added')
                    finally:
                        advance.set(); applying.set(); finish.set(); browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

    def test_description_editor_survives_packet_delay_and_job_falling_out_of_loaded_board(self):
        import uvicorn
        from playwright.sync_api import sync_playwright, expect
        from career_agent.dashboard import DashboardConfig, create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'test.sqlite3'
            job = Job.from_mapping({'company': 'Fictional Co', 'title': 'Fictional role',
                'job_url': 'https://example.invalid/role/1', 'description': 'Original description.'})
            with Store(db) as store:
                store.add_jobs([job])
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
                            window.holdPacket = true; window.omitBoard = false;
                            window.fetch = (url, options) => {
                              if (window.holdPacket && String(url).endsWith('/packet')) {
                                return new Promise((resolve, reject) => {
                                  window.releasePacket = () => { window.holdPacket = false; realFetch(url, options).then(resolve, reject); };
                                });
                              }
                              if (window.omitBoard && String(url).startsWith('/api/jobs?limit=')) {
                                return Promise.resolve(new Response(JSON.stringify({jobs: [], next_before: null}),
                                  {status: 200, headers: {'Content-Type': 'application/json'}}));
                              }
                              return realFetch(url, options);
                            };
                        ''')
                        page.goto(f'http://127.0.0.1:{port}')
                        expect(page.locator('#job-list')).to_contain_text('Fictional role')
                        page.get_by_role('button', name='View Fictional role at Fictional Co').click()
                        page.get_by_text('Correct saved description', exact=True).click()
                        editor = page.locator('#job-description-editor')
                        editor.locator('textarea').nth(0).fill('Revised fictional description.')
                        editor.locator('textarea').nth(1).fill('Fictional source note')
                        page.evaluate('''() => {
                            window.detailRerendered = false;
                            new MutationObserver(() => {window.detailRerendered = true;}).observe(
                              document.getElementById('detail-panel'), {childList: true});
                            window.releasePacket();
                        }''')
                        page.wait_for_function('() => window.detailRerendered === true')
                        expect(editor.locator('textarea').nth(0)).to_have_value('Revised fictional description.')
                        expect(editor.locator('textarea').nth(1)).to_have_value('Fictional source note')
                        page.evaluate('window.omitBoard = true')
                        page.locator('#refresh').click()
                        expect(page.locator('#detail-panel')).to_contain_text('Opportunity outside the loaded board')
                        expect(editor.locator('textarea').nth(0)).to_have_value('Revised fictional description.')
                        expect(editor.locator('textarea').nth(1)).to_have_value('Fictional source note')
                        editor.get_by_role('checkbox').check()
                        editor.get_by_role('button', name='Save corrected description').click()
                        expect(page.locator('#notice')).to_contain_text('Description corrected')
                        with Store(db) as store:
                            self.assertEqual(store.get_job(job.id).description, 'Revised fictional description.')
                            self.assertEqual(store.description_revision(job.id), 1)
                    finally:
                        browser.close()
            finally:
                server.should_exit = True; thread.join(timeout=10); sock.close()

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

    def test_open_board_and_h1b_views_update_at_provider_deadline(self):
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
                observation = {'availability': 'listed', 'reason': 'Fictional check',
                    'source_url': 'https://example.invalid/role', 'checked_at': datetime.now(timezone.utc).isoformat(),
                    'application_deadline': (datetime.now(timezone.utc) + timedelta(seconds=8)).isoformat(), 'stale': False}
                with patch('career_agent.posting_checks.PostingChecks.latest', return_value=observation), sync_playwright() as p:
                    browser = p.chromium.launch()
                    try:
                        page = browser.new_page(); page.goto(f'http://127.0.0.1:{port}')
                        expect(page.locator('#job-list')).to_contain_text('Listed at check')
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        results = page.locator('#h1b-opportunities-results')
                        expect(results).to_contain_text('listed')
                        expect(results).to_contain_text('deadline passed; availability unknown', timeout=14000)
                        expect(page.locator('#job-list')).to_contain_text('Deadline passed', timeout=14000)
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
                        page.get_by_label('Sort opportunities', exact=True).select_option('recent')
                        expect(page.locator('#board-sort')).to_have_value('recent')
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
                        expect(page.get_by_role('heading', name='Data administration')).to_be_visible()
                        release = {'source_url': 'https://www.dol.gov/agencies/eta/foreign-labor/performance',
                                   'checked_at': '2026-08-15T12:00:00+00:00', 'fiscal_year': 2026, 'quarter': 3,
                                   'workbook_url': 'https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx',
                                   'notice': 'Coverage is not independently verified.'}
                        with patch('career_agent.dol_releases.check_dol_releases', return_value=release) as check:
                            page.locator('#h1b-release-consent').check()
                            page.get_by_role('button', name='Check DOL releases').click()
                            expect(page.locator('#h1b-release-status')).to_contain_text('FY 2026 Q3')
                            expect(page.locator('#h1b-coverage')).to_contain_text('0 imported')
                            page.get_by_role('button', name='Use this workbook URL').click()
                            expect(page.locator('#h1b-refresh-url')).to_have_value(release['workbook_url'])
                            expect(page.locator('#h1b-refresh-consent')).not_to_be_checked()
                            check.assert_called_once_with()
                            page.get_by_text('Import public DOL LCA records', exact=True).click()
                        expect(page.locator('#h1b-coverage')).to_contain_text('0 imported')
                        expect(page.locator('#h1b-opportunities-status')).to_contain_text('2 matching opportunities')
                        expect(page.locator('#h1b-opportunities-results')).to_contain_text('Transfer role')
                        transfer = page.locator('#h1b-opportunities-results section').filter(has=page.get_by_role('heading', name='Transfer role · Example Inc.')).first
                        transfer.get_by_text('Recruiter or employer policy notes', exact=True).click()
                        transfer.get_by_label('Discussion date').fill('2025-05-01')
                        transfer.get_by_label('Who or what was the source?').fill('Fictional recruiter')
                        transfer.get_by_label('H-1B pathway discussed').select_option('transfer')
                        transfer.get_by_label('Reported response').select_option('conditional')
                        transfer.get_by_label('What was said, including any conditions?').fill('<script>throw new Error("unsafe")</script> Only if approved.')
                        transfer.get_by_label('I understand this is my unverified record, not a guarantee.').check()
                        transfer.get_by_role('button', name='Save policy note').click()
                        expect(transfer).to_contain_text('Policy note saved')
                        expect(transfer).to_contain_text('<script>throw new Error("unsafe")</script> Only if approved.')
                        page.reload()
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-opportunities-status')).to_contain_text('2 matching opportunities')
                        transfer = page.locator('#h1b-opportunities-results section').filter(has=page.get_by_role('heading', name='Transfer role · Example Inc.')).first
                        transfer.get_by_text('Recruiter or employer policy notes', exact=True).click()
                        transfer.get_by_role('button', name='Load policy notes').click()
                        expect(transfer).to_contain_text('Fictional recruiter')
                        expect(transfer).to_contain_text('Only if approved.')
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
                        expect(page.locator('#h1b-import-status')).to_contain_text('1 added')
                        page.get_by_text('Recent DOL import history (up to 10)').click()
                        expect(page.locator('#h1b-coverage')).to_contain_text('observed decisions 2025-04-15–2025-04-15')
                        expect(page.locator('#h1b-coverage')).to_contain_text('selected-field SHA-256')
                        expect(page.locator('#h1b-coverage')).to_contain_text('not a verified reporting period')
                        try:
                            from openpyxl import Workbook
                            import defusedxml  # noqa: F401
                        except ImportError:
                            pass  # Workbook support is an optional extra.
                        else:
                            from io import BytesIO
                            book = Workbook()
                            book.active.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
                            book.active.append(['I-200-25123-000099', 'Fictional Workbook', 'H-1B', 'Certified', '2025-04-15'])
                            buffer = BytesIO(); book.save(buffer); book.close()
                            page.locator('#h1b-file').set_input_files({'name': 'fictional.xlsx',
                                'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': buffer.getvalue()})
                            page.locator('#h1b-consent').check()
                            page.get_by_role('button', name='Import records locally', exact=True).click()
                            expect(page.locator('#h1b-import-status')).to_contain_text('1 added')
                            expect(page.locator('#h1b-coverage')).to_contain_text('2 imported H-1B cases')
                            import hashlib
                            content = buffer.getvalue()
                            def fake_download(_url, _hosts, destination, **_kwargs):
                                Path(destination).write_bytes(content)
                                return {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                            with patch('career_agent.public_sources.public_download', side_effect=fake_download):
                                page.locator('#h1b-refresh-url').fill('https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx')
                                page.locator('#h1b-refresh-consent').check()
                                page.get_by_role('button', name='Download and import DOL workbook').click()
                                expect(page.locator('#h1b-refresh-status')).to_contain_text('1 updated')
                                page.get_by_text('Recent DOL import history (up to 10)').click()
                                expect(page.locator('#h1b-coverage')).to_contain_text('downloaded over DOL HTTPS')
                                expect(page.locator('#h1b-coverage')).to_contain_text('original-file SHA-256')
                        page.get_by_text('USCIS employer decisions: refresh or import', exact=True).click()
                        page.locator('#uscis-source').fill(URL)
                        page.locator('#uscis-year').fill('2025')
                        page.locator('#uscis-as-of').fill('2025-09-30')
                        page.locator('#uscis-file').set_input_files({'name': 'uscis-fictional.csv', 'mimeType': 'text/csv',
                            'buffer': hub_csv([{'Employer': 'Example Inc.'}])})
                        page.locator('#uscis-consent').check()
                        page.get_by_role('button', name='Import official CSV locally').click()
                        expect(page.locator('#uscis-status')).to_contain_text('1 employer groups')
                        with patch('career_agent.uscis.public_get', return_value=(403, b'', 'text/html')):
                            page.locator('#uscis-consent').check()
                            page.get_by_role('button', name='Refresh from USCIS').click()
                            expect(page.locator('#uscis-status')).to_contain_text('HTTP 403')
                            expect(page.locator('#uscis-status')).to_contain_text('No USCIS API token is needed')
                        page.route('**/api/h1b/uscis/refresh', lambda route: route.fulfill(
                            status=403, content_type='application/json', body='{"detail":"Session token required"}'))
                        page.get_by_role('button', name='Refresh from USCIS').click()
                        expect(page.locator('#uscis-status')).to_contain_text('refresh this page')
                        expect(page.locator('#uscis-status')).to_contain_text('no USCIS token is required')
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
                        page.locator('#h1b-query').fill('Example, Inc')
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-status')).to_contain_text('0 stored jobs')
                        page.locator('#h1b-broad-names').check()
                        expect(page.locator('#h1b-results')).to_be_empty()
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-status')).to_contain_text('1 stored jobs')
                        expect(page.locator('#h1b-results')).to_contain_text('Transfer role')
                        page.locator('#h1b-broad-names').uncheck()
                        page.locator('#h1b-query').fill('Unmatched Company')
                        expect(page.locator('#h1b-results')).to_be_empty()
                        page.get_by_role('button', name='Search company records and jobs').click()
                        expect(page.locator('#h1b-results')).to_contain_text('No employer records found')
                        page.reload()
                        page.get_by_role('button', name='H-1B research', exact=True).click()
                        expect(page.locator('#h1b-coverage')).to_contain_text('Recent DOL import history')
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
                            def delayed(_route, _prompt, *, json_schema=None):
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
