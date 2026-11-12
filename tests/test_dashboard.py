"""Offline FastAPI workspace tests with a fictional candidate and no network calls."""
from __future__ import annotations

import importlib.util
import asyncio
import base64
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict, replace
from pathlib import Path

from career_agent.application import load_packet
from career_agent import ai
from career_agent.models import CandidateProfile, Job
from career_agent.storage import Store

HAS_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


def fake_fact_bank():
    return {
        "name": "Fictional Candidate",
        "headline": "AI Program Delivery",
        "facts": [{
            "id": "experience-01",
            "section": "experience",
            "statement": "Delivered a fictional AI program involving Python and AWS.",
            "keywords": ["Python", "AWS"],
            "source_note": "Private fictional evidence that must never appear in the packet.",
            "verified_by_candidate": True,
        }],
    }


def fake_job(url="https://example.invalid/jobs/alpha"):
    return Job.from_mapping({
        "company": "Example Company",
        "title": "AI Program Lead",
        "location": "Remote USA",
        "job_url": url,
        "description": "Seeking Python and AWS. Visa sponsorship is available.",
        "is_remote": True,
    })


@unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
class DashboardTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "career.sqlite3"
        self.packet_dir = self.root / "packets"
        self.profile_path = self.root / "preferences.json"
        self.facts_path = self.root / "facts.json"
        profile = CandidateProfile(
            target_titles=("AI Program Lead",),
            skills=("Python", "AWS"),
            preferred_locations=(),
            allow_remote=True,
        )
        self.profile_path.write_text(json.dumps(asdict(profile)))
        self.facts_path.write_text(json.dumps(fake_fact_bank()))
        self.job = fake_job()
        with Store(self.db) as store:
            self.assertEqual(store.add_jobs([self.job]), 1)
        self.app = create_app(DashboardConfig(
            db_path=self.db,
            packet_dir=self.packet_dir,
            profile_path=self.profile_path,
            facts_path=self.facts_path,
        ))
        self.client = TestClient(self.app, base_url="http://localhost")
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        self.assertIn("data-session=", landing.text)
        match = re.search(r'data-session="([^"]+)"', landing.text)
        self.assertIsNotNone(match)
        self.session = match.group(1)

    def call(self, method, path, payload=None, token=None, headers=None):
        values = {"X-Session-Token": self.session if token is None else token}
        values.update(headers or {})
        if payload is None:
            return self.client.request(method, path, headers=values)
        return self.client.request(method, path, headers=values, json=payload)

    def test_oversized_request_is_rejected(self):
        from career_agent.dashboard import API_BODY_LIMIT

        response = self.client.post(
            '/api/workspace/resume-extract',
            headers={'X-Session-Token': self.session, 'Content-Type': 'application/json'},
            content=b'x' * (3 * 1024 * 1024 + 1),
        )
        self.assertEqual(response.status_code, 413)
        response = self.client.post(
            '/api/jobs',
            headers={'X-Session-Token': self.session, 'Content-Type': 'application/json'},
            content=b'x' * (API_BODY_LIMIT + 1),
        )
        self.assertEqual(response.status_code, 413)

    def test_dol_refresh_requires_explicit_consent_and_existing_session(self):
        url = 'https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx'
        payload = {'source_url': url, 'acknowledged': True}
        with patch('career_agent.h1b.H1BResearch.refresh_dol', return_value={'added': 1}) as refresh:
            self.assertEqual(self.call('POST', '/api/h1b/refresh-dol', payload, token='bad').status_code, 403)
            self.assertEqual(self.call('POST', '/api/h1b/refresh-dol', {**payload, 'acknowledged': False}).status_code, 422)
            self.assertEqual(self.call('POST', '/api/h1b/refresh-dol', payload, headers={'Origin': 'https://example.invalid'}).status_code, 403)
            refresh.assert_not_called()
            self.assertEqual(self.call('POST', '/api/h1b/refresh-dol', payload).json()['added'], 1)
            refresh.assert_called_once()

    def test_dol_workbook_upload_is_bounded_authorized_and_atomic(self):
        try:
            from openpyxl import Workbook
            import defusedxml  # noqa: F401
        except ImportError:
            self.skipTest('Install optional LCA workbook support')
        from io import BytesIO
        path = '/api/h1b/import-workbook?source_url=https%3A%2F%2Fwww.dol.gov%2Fdata&captured_on=2025-06-01'
        headers = {'X-Session-Token': self.session, 'X-LCA-Acknowledged': 'true', 'Content-Type': 'application/octet-stream'}
        def workbook(rows):
            book = Workbook()
            book.active.append(['CASE_NUMBER', 'EMPLOYER_NAME', 'VISA_CLASS', 'CASE_STATUS', 'DECISION_DATE'])
            for row in rows:
                book.active.append(row)
            output = BytesIO(); book.save(output); book.close()
            return output.getvalue()
        valid = ['I-200-25123-000001', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15']
        content = workbook([valid])
        self.assertEqual(self.client.post(path, content=content, headers={**headers, 'X-Session-Token': 'bad'}).status_code, 403)
        self.assertEqual(self.client.post(path, content=content, headers={**headers, 'Origin': 'https://example.invalid'}).status_code, 403)
        self.assertEqual(self.client.post(path, content=content, headers={**headers, 'X-LCA-Acknowledged': 'false'}).status_code, 422)
        self.assertEqual(self.client.post(path, content=content, headers={**headers, 'Content-Type': 'application/json'}).status_code, 415)
        self.assertEqual(self.client.post(path, content=b'not a workbook', headers=headers).status_code, 422)
        self.assertEqual(self.client.post(path, content=content, headers={**headers, 'Content-Length': str(len(content) + 1)}).status_code, 400)
        with patch('career_agent.dashboard.LCA_WORKBOOK_LIMIT', 10):
            self.assertEqual(self.client.post(path, content=content, headers=headers).status_code, 413)
            import httpx
            consumed = []
            async def chunks():
                consumed.append('first'); yield b'12345678901'
                consumed.append('second'); yield b'x'
            async def send():
                transport = httpx.ASGITransport(app=self.app)
                async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
                    return await client.post(path, content=chunks(), headers=headers)
            self.assertEqual(asyncio.run(send()).status_code, 413)
            self.assertEqual(consumed, ['first'])
        bad = ['not-a-case', 'Fictional Employer', 'H-1B', 'Certified', '2025-04-15']
        self.assertEqual(self.client.post(path, content=workbook([valid, bad]), headers=headers).status_code, 422)
        self.assertEqual(self.call('GET', '/api/h1b').json()['coverage']['records'], 0)
        response = self.client.post(path, content=content, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['added'], 1)
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(self.call('GET', '/api/h1b').json()['coverage']['records'], 1)

    def test_dol_workbook_progress_is_visible_only_during_import(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        started, release = Event(), Event()
        def blocked_import(_research, _path, _url, _date, *, progress):
            progress('scanning', 2000)
            started.set()
            if not release.wait(10):
                raise RuntimeError('Test did not release importer')
            return {'added': 0}
        headers = {'X-Session-Token': self.session, 'X-LCA-Acknowledged': 'true', 'Content-Type': 'application/octet-stream'}
        path = '/api/h1b/import-workbook?source_url=https%3A%2F%2Fwww.dol.gov%2Fdata&captured_on=2025-06-01'
        self.assertEqual(self.call('GET', '/api/h1b/import-progress').json()['phase'], 'idle')
        with patch('career_agent.dashboard.H1BResearch.import_xlsx', blocked_import), ThreadPoolExecutor() as executor:
            future = executor.submit(self.client.post, path, content=b'workbook', headers=headers)
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(self.call('GET', '/api/h1b/import-progress').json(), {'phase': 'scanning', 'amount': 2000})
                self.assertEqual(self.client.post(path, content=b'other', headers=headers).status_code, 409)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10).status_code, 200)
        self.assertEqual(self.call('GET', '/api/h1b/import-progress').json()['phase'], 'idle')
    def test_oversized_stream_stops_reading(self):
        import httpx
        from career_agent.dashboard import API_BODY_LIMIT

        consumed = []

        async def body():
            consumed.append('first')
            yield b'x' * (API_BODY_LIMIT + 1)
            consumed.append('second')
            yield b'x'

        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
                return await client.post('/api/jobs', content=body(), headers={
                    'X-Session-Token': self.session, 'Content-Type': 'application/json',
                })

        self.assertEqual(asyncio.run(send()).status_code, 413)
        self.assertEqual(consumed, ['first'])

    def test_resume_extract_is_local_and_does_not_change_workspace(self):
        before = self.call('GET', '/api/workspace').json()
        payload = {'filename': 'fictional.txt', 'content': base64.b64encode(b'Fictional experience').decode()}
        self.assertEqual(self.call('POST', '/api/workspace/resume-extract', payload, token='bad').status_code, 403)
        response = self.call('POST', '/api/workspace/resume-extract', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['text'], 'Fictional experience')
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)
        self.assertEqual(self.call('POST', '/api/workspace/resume-extract', {'filename': 'x.pdf', 'content': '!!!!'}).status_code, 422)

    def test_fit_evidence_uses_only_verified_statements_and_never_private_notes(self):
        job = Job.from_mapping({'company': 'Fictional Corp', 'title': 'Data Analyst',
            'job_url': 'https://example.invalid/requirements',
            'description': 'Required qualifications:\n- Python experience\n- Rust experience'})
        with Store(self.db) as store:
            store.add_jobs([job])
        path = f'/api/jobs/{job.id}/fit-evidence'
        self.assertEqual(self.client.get(path).status_code, 403)
        before = self.call('GET', '/api/workspace').json()
        result = self.call('GET', path).json()
        self.assertEqual(result['requirements'][0]['suggested_facts'][0]['id'], 'experience-01')
        self.assertEqual(result['requirements'][1]['suggested_facts'], [])
        self.assertNotIn('Private fictional evidence', json.dumps(result))
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)
        imported = self.call('POST', '/api/workspace/import', {'acknowledged': True}).json()['workspace']
        new = dict(imported['facts'][0], id='unverified-02', statement='Unverified Python experience',
                   source_note='Do not transmit.', verified_by_candidate=False)
        saved = self.call('PUT', '/api/workspace', {'expected_revision': imported['revision'],
            'profile': imported['profile'], 'facts': imported['facts'] + [new]}).json()['workspace']
        rows = self.call('GET', path).json()['requirements'][0]['suggested_facts']
        self.assertEqual([fact['id'] for fact in rows], ['experience-01'])
        self.assertFalse(saved['facts'][-1]['verified_by_candidate'])

    def test_ai_analysis_requires_consent_and_preserves_application_state(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value='{"requirements":[]}') as complete:
            result = self.call('GET', '/api/ai/routes')
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('key_env', result.text)
            url = f'/api/jobs/{self.job.id}/ai-analysis'
            self.assertEqual(self.call('POST', url, {'route': 'local'}).status_code, 422)
            complete.assert_not_called()
            request = {'route': 'local', 'route_fingerprint': ai.route_fingerprint('local', route['local']), 'acknowledged': True}
            self.assertEqual(self.call('POST', url, request).status_code, 200)
            with patch('career_agent.ai.complete', side_effect=ValueError('Provider unavailable')):
                self.assertEqual(self.call('POST', url, request).status_code, 422)
            self.assertEqual(self.call('POST', url, request).status_code, 200)
            self.assertFalse(self.packet_dir.exists())

    def test_ai_tailoring_is_consent_gated_and_read_only(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        output = {'suggestions': [{'fact_id': 'experience-01', 'job_quote': 'Python', 'wording': 'Fictional rewrite.'}]}
        url = f'/api/jobs/{self.job.id}/ai-tailoring-suggestions'
        before = self.call('GET', '/api/workspace').json()
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete:
            self.assertEqual(self.call('POST', url, {'route': 'local'}).status_code, 422)
            complete.assert_not_called()
            response = self.call('POST', url, {'route': 'local', 'route_fingerprint': ai.route_fingerprint('local', route['local']), 'acknowledged': True})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['suggestions'][0]['fact_id'], 'experience-01')
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)
        self.assertFalse(self.packet_dir.exists())

    def test_ai_resume_suggestions_are_consented_read_only_and_budgeted(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1', 'daily_request_limit': 1}}
        resume = 'Built a fictional Python dashboard.'
        output = {'suggestions': [{'quote': resume, 'section': 'experience'}]}
        path = '/api/workspace/ai-resume-suggestions'
        before = self.call('GET', '/api/workspace').json()
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value=json.dumps(output)) as complete:
            fingerprint = self.call('GET', '/api/ai/routes').json()['routes'][0]['route_fingerprint']
            self.assertEqual(self.call('POST', path, {'route': 'local', 'resume_text': resume}).status_code, 422)
            complete.assert_not_called()
            request = {'route': 'local', 'route_fingerprint': fingerprint, 'resume_text': resume, 'acknowledged': True}
            changed_route = {'local': {**route['local'], 'base_url': 'http://127.0.0.1:1234/v1'}}
            with patch('career_agent.ai.routes', return_value=changed_route):
                changed = self.call('POST', path, request)
                self.assertEqual(changed.status_code, 422)
                self.assertIn('reload routes', changed.json()['detail'])
                complete.assert_not_called()
            response = self.call('POST', path, request)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['suggestions'], output['suggestions'])
            self.assertEqual(self.call('POST', path, request).status_code, 429)
            self.assertEqual(complete.call_count, 1)
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)
        history = self.call('GET', '/api/ai/history').json()
        self.assertEqual(history['recent'][0]['purpose'], 'resume_fact_suggestions')
        self.assertNotIn(resume, json.dumps(history))

    def test_ai_route_test_requires_consent_and_releases_lock_on_failure(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.dashboard.ai.test_route', return_value={'notice': 'ok'}) as probe:
            path = '/api/ai/test-route'
            self.assertEqual(self.call('POST', path, {'route': 'local'}).status_code, 422)
            probe.assert_not_called()
            request = {'route': 'local', 'route_fingerprint': ai.route_fingerprint('local', route['local']), 'acknowledged': True}
            self.assertEqual(self.call('POST', path, request).status_code, 200)
            with patch('career_agent.dashboard.ai.test_route', side_effect=ValueError('unavailable')):
                self.assertEqual(self.call('POST', path, request).status_code, 422)
            self.assertEqual(self.call('POST', path, request).status_code, 200)

    def test_ai_history_limit_applies_across_dashboard_features(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1', 'daily_request_limit': 1}}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value='OK') as complete:
            fingerprint = ai.route_fingerprint('local', route['local'])
            self.assertEqual(self.call('POST', '/api/ai/test-route', {'route': 'local', 'route_fingerprint': fingerprint, 'acknowledged': True}).status_code, 200)
            denied = self.call('POST', '/api/workspace/chat', {'route': 'local', 'route_fingerprint': fingerprint, 'message': 'Fictional private question', 'acknowledged': True})
            self.assertEqual(denied.status_code, 429)
            self.assertEqual(complete.call_count, 1)
            history = self.call('GET', '/api/ai/history').json()
            self.assertEqual(history['today'], [{'route': 'local', 'attempts': 1}])
            self.assertEqual(history['recent'][0]['purpose'], 'route_test')
            self.assertNotIn('private question', json.dumps(history))

    def test_changed_route_never_transmits_or_consumes_quota_across_ai_features(self):
        local = {'provider': 'ollama', 'model': 'local-fictional', 'base_url': 'http://127.0.0.1:11434/v1'}
        cloud = {'provider': 'anthropic', 'model': 'cloud-fictional', 'daily_request_limit': 1}
        cases = [
            ('/api/ai/test-route', {}),
            ('/api/workspace/chat', {'message': 'Fictional private question'}),
            ('/api/workspace/ai-resume-suggestions', {'resume_text': 'Fictional private resume text.'}),
            (f'/api/jobs/{self.job.id}/ai-analysis', {}),
            (f'/api/jobs/{self.job.id}/ai-tailoring-suggestions', {}),
        ]
        with patch('career_agent.ai.routes', return_value={'selected': cloud}), patch('career_agent.ai.complete') as complete:
            for path, extra in cases:
                request = {'route': 'selected', 'route_fingerprint': ai.route_fingerprint('selected', local),
                           'acknowledged': True, **extra}
                with self.subTest(path=path):
                    response = self.call('POST', path, request)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn('reload routes', response.json()['detail'])
            complete.assert_not_called()
            self.assertEqual(self.call('GET', '/api/ai/history').json()['recent'], [])

    def test_profile_chat_sends_only_opted_in_verified_statements(self):
        path = '/api/workspace/chat'
        request = {'route': 'local', 'message': 'What should I highlight?'}
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        request['route_fingerprint'] = ai.route_fingerprint('local', route['local'])
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.dashboard.ai.profile_chat', return_value={
            'answer': 'Review your evidence.', 'route': 'local', 'provider': 'ollama', 'model': 'fictional'
        }) as chat:
            self.assertEqual(self.call('POST', path, request).status_code, 422)
            chat.assert_not_called()
            self.assertEqual(self.call('POST', path, request | {'acknowledged': True}).status_code, 200)
            chat.assert_called_with('local', request['message'], {}, True, [])
            self.assertEqual(self.call('POST', path, request | {
                'acknowledged': True, 'include_verified_facts': 'true'
            }).status_code, 422)
            imported = self.call('POST', '/api/workspace/import', {'acknowledged': True}).json()['workspace']
            unverified = dict(imported['facts'][0], id='experience-02', statement='An unverified fictional statement.',
                              verified_by_candidate=False, source_note='Secret note.')
            saved = self.call('PUT', '/api/workspace', {
                'expected_revision': imported['revision'], 'profile': imported['profile'],
                'facts': imported['facts'] + [unverified],
            }).json()['workspace']
            result = self.call('POST', path, request | {'acknowledged': True, 'include_verified_facts': True})
            self.assertEqual(result.status_code, 200)
            chat.assert_called_with('local', request['message'], {
                'experience-01': fake_fact_bank()['facts'][0]['statement']
            }, True, [])
            self.assertEqual(self.call('GET', '/api/workspace').json()['workspace']['revision'], saved['revision'])

    def test_schedule_api_requires_consent_and_supports_pause_remove(self):
        data = {'query': 'Fictional role', 'location': 'USA', 'sites': ['indeed'], 'interval_minutes': 360, 'results': 5}
        self.assertEqual(self.call('POST', '/api/automation/searches', data).status_code, 422)
        saved = self.call('POST', '/api/automation/searches', data | {'acknowledged': True}).json()
        path = '/api/automation/searches/' + saved['id']
        self.assertEqual(self.call('POST', path, {'remove': True, 'acknowledged': True}).status_code, 422)
        self.assertEqual(self.call('POST', path, {'enabled': False, 'acknowledged': True}).status_code, 200)
        self.assertEqual(self.call('POST', path, {'remove': True, 'acknowledged': True}).status_code, 200)
        self.assertEqual(self.call('GET', '/api/automation').json()['schedules'], [])

    def test_legacy_profile_change_invalidates_durable_packet_and_review(self):
        fingerprint = self._draft()
        data = fake_fact_bank()
        data['facts'][0]['statement'] = 'A different fictional career statement involving Python.'
        self.facts_path.write_text(json.dumps(data))
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertTrue(packet['stale'])
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 409)

    def test_legacy_packet_review_does_not_require_configured_profile(self):
        from career_agent.application import build_packet, save_packet
        from career_agent.candidate import FactBank
        from career_agent.dashboard import create_app, DashboardConfig
        from fastapi.testclient import TestClient
        packet = build_packet(self.job, CandidateProfile.load(self.profile_path), FactBank.parse(fake_fact_bank()))
        save_packet(packet, self.packet_dir / (self.job.id + '.json'))
        app = create_app(DashboardConfig(db_path=self.db, packet_dir=self.packet_dir))
        with TestClient(app, base_url='http://localhost') as client:
            landing = client.get('/')
            token = re.search(r'data-session="([^"]+)"', landing.text).group(1)
            response = client.post(f'/api/jobs/{self.job.id}/decision', headers={'X-Session-Token': token},
                json={'approved': True, 'acknowledged': True, 'packet_sha256': packet['packet_sha256']})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['decision'], 'approved_for_manual_submission')

    def test_uscis_refresh_failure_and_live_check_require_explicit_action(self):
        from test_uscis import hub_csv, URL

        data = {'source_url': URL, 'fiscal_year': 2025, 'data_as_of': '2025-09-30',
                'acknowledged': True, 'csv': hub_csv().decode()}
        self.assertEqual(self.call('POST', '/api/h1b/uscis/import', data).status_code, 200)
        with patch('career_agent.uscis.public_get', return_value=(403, b'', 'text/html')) as fetch:
            self.assertEqual(self.call('POST', '/api/h1b/uscis/refresh', {**data, 'acknowledged': False}).status_code, 422)
            fetch.assert_not_called()
            response = self.call('POST', '/api/h1b/uscis/refresh', data)
        self.assertEqual(response.status_code, 422)
        self.assertIn('403', response.json()['detail'])
        self.assertEqual(len(self.call('GET', '/api/h1b').json()['coverage']['uscis_snapshots']), 1)
        path = f'/api/jobs/{self.job.id}/live-check'
        with patch('career_agent.dashboard.check_posting', return_value={
                'availability': 'unknown', 'reason': 'Unsupported posting',
                'checked_at': '2025-01-01T00:00:00+00:00', 'source_url': self.job.url, 'policy': None}) as check:
            self.assertEqual(self.call('POST', path, {}).status_code, 422)
            self.assertEqual(self.call('POST', path, {'acknowledged': True}, token='bad').status_code, 403)
            check.assert_not_called()
            self.assertEqual(self.call('POST', path, {'acknowledged': True}).status_code, 200)
        observed = self.call('GET', f'/api/jobs/{self.job.id}/h1b').json()['live_check']
        self.assertEqual(observed['availability'], 'unknown')
        self.assertTrue(observed['stale'])
        board_job = next(job for job in self.call('GET', '/api/jobs').json()['jobs'] if job['id'] == self.job.id)
        self.assertFalse(board_job['live_check_supported'])
        self.assertTrue(board_job['live_check']['stale'])
        self.assertEqual(board_job['live_check']['availability'], 'unknown')

    def test_board_exposes_saved_observation_without_checking_network(self):
        from datetime import datetime, timezone
        from career_agent.posting_checks import PostingChecks

        job = fake_job('https://boards.greenhouse.io/example/jobs/123456')
        with Store(self.db) as store:
            store.add_jobs([job])
            PostingChecks(store).save(job.id, {
                'availability': 'listed', 'reason': 'Public posting found',
                'checked_at': datetime.now(timezone.utc).isoformat(),
                'source_url': job.url, 'policy': None,
            })
        with patch('career_agent.dashboard.check_posting') as check:
            jobs = self.call('GET', '/api/jobs').json()['jobs']
            check.assert_not_called()
        board_job = next(item for item in jobs if item['id'] == job.id)
        self.assertTrue(board_job['live_check_supported'])
        self.assertFalse(board_job['live_check']['stale'])
        self.assertEqual(board_job['live_check']['availability'], 'listed')
        unchecked = next(item for item in jobs if item['id'] == self.job.id)
        self.assertIsNone(unchecked['live_check'])

    def test_h1b_settings_import_search_and_job_assessment(self):
        from test_h1b import lca_csv
        from career_agent.h1b import DOL_URL

        self.assertEqual(self.client.get('/api/h1b').status_code, 403)
        settings = {'need': 'new', 'min_year': 2024, 'include_unknown': True}
        self.assertEqual(self.call('PUT', '/api/h1b', settings).json(), settings)
        self.assertEqual(self.call('GET', '/api/h1b').json()['settings'], settings)
        data = {'csv': lca_csv([{'EMPLOYER_NAME': self.job.company}]), 'source_url': DOL_URL,
                'captured_on': '2025-06-01', 'acknowledged': True}
        self.assertEqual(self.call('POST', '/api/h1b/import', {**data, 'acknowledged': False}).status_code, 422)
        self.assertEqual(self.call('POST', '/api/h1b/import', data, headers={'Origin': 'https://example.invalid'}).status_code, 403)
        self.assertEqual(self.call('POST', '/api/h1b/import', data).json()['added'], 1)
        response = self.call('GET', '/api/h1b/search?q=Example')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['companies'][0]['recent_certified_cases'], 1)
        self.assertEqual(response.json()['jobs'][0]['policy'], 'possible')
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/h1b').json()['need'], 'new')
        self.assertEqual(self.call('GET', '/api/h1b/search?q=x').status_code, 422)
        self.assertEqual(self.call('GET', '/api/h1b/search?q=Example&offset=-1').status_code, 422)
        self.assertEqual(self.call('GET', '/api/jobs/' + 'f' * 16 + '/h1b').status_code, 404)
        self.assertEqual(self.call('POST', '/api/h1b/import', {**data, 'csv': data['csv'] + '\n"unterminated'}).status_code, 422)
        self.assertEqual(self.call('GET', '/api/h1b').json()['coverage']['records'], 1)

    def test_h1b_employer_association_endpoint(self):
        from test_h1b import lca_csv
        from career_agent.h1b import DOL_URL
        self.call('POST', '/api/h1b/import', {'csv': lca_csv([{'EMPLOYER_NAME': 'Fictional Legal LLC'}]),
                  'source_url': DOL_URL, 'captured_on': '2025-06-01', 'acknowledged': True})
        path = f'/api/jobs/{self.job.id}/h1b/employer'
        data = {'employer': 'Fictional Legal LLC', 'source_note': 'Checked careers page',
                'expected_revision': 0, 'acknowledged': True}
        self.assertEqual(self.call('PUT', path, data, token='bad').status_code, 403)
        self.assertEqual(self.call('PUT', path, {**data, 'acknowledged': False}).status_code, 422)
        self.assertEqual(self.call('PUT', path, {**data, 'employer': 'Missing'}).status_code, 422)
        self.assertEqual(self.call('PUT', '/api/jobs/' + 'f' * 16 + '/h1b/employer', data).status_code, 404)
        original = self.call('GET', f'/api/jobs/{self.job.id}/h1b').json()
        result = self.call('PUT', path, data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['policy'], original['policy'])
        self.assertEqual(result.json()['history']['recent_certified_cases'], 1)
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/h1b').json()['employer_association']['employer'], 'Fictional Legal LLC')
        self.assertEqual(self.call('PUT', path, data).status_code, 422)

    def test_h1b_company_suggestion_endpoint_requires_review(self):
        from test_h1b import lca_csv
        from career_agent.h1b import DOL_URL
        self.call('POST', '/api/h1b/import', {'csv': lca_csv([{'EMPLOYER_NAME': 'Fictional Legal LLC'}]),
                  'source_url': DOL_URL, 'captured_on': '2025-06-01', 'acknowledged': True})
        path = f'/api/jobs/{self.job.id}/h1b/company-association'
        data = {'employer': 'Fictional Legal LLC', 'source_note': 'Checked careers page',
                'expected_revision': 0, 'acknowledged': True}
        self.assertEqual(self.call('GET', path, token='bad').status_code, 403)
        self.assertEqual(self.call('PUT', path, data, token='bad').status_code, 403)
        self.assertEqual(self.call('PUT', path, {**data, 'acknowledged': False}).status_code, 422)
        saved = self.call('PUT', path, data)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['association']['revision'], 1)
        job = self.call('GET', f'/api/jobs/{self.job.id}/h1b').json()
        self.assertEqual(job['company_association']['employer'], 'Fictional Legal LLC')
        self.assertIsNone(job['employer_association'])
        self.assertNotEqual(job['history']['employer'], 'Fictional Legal LLC')
        self.assertEqual(self.call('PUT', path, data).status_code, 422)
        self.assertEqual(self.call('GET', path).json()['history'][0]['employer'], 'Fictional Legal LLC')

    def test_tracker_export_is_private_download_and_excludes_career_files(self):
        from career_agent.crm import ApplicationCRM

        with Store(self.db) as store:
            ApplicationCRM(store).add_contact(self.job.id, "Fictional Renée", notes="Private contact note")
        self.assertEqual(self.client.get("/api/crm/export").status_code, 403)
        self.assertEqual(self.call("GET", "/api/crm/export", token="wrong").status_code, 403)
        self.assertEqual(self.call("GET", "/api/crm/export", headers={"Origin": "https://example.invalid"}).status_code, 403)
        response = self.call("GET", "/api/crm/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-disposition"],
                         'attachment; filename="career-tracker-history.json"')
        rows = response.json()["records"]
        self.assertEqual(rows["jobs"][0]["job_id"], self.job.id)
        self.assertEqual(rows["contacts"][0]["name"], "Fictional Renée")
        self.assertIn("Private contact note", response.text)
        self.assertNotIn(fake_fact_bank()["facts"][0]["source_note"], response.text)
        self.assertNotIn(self.session, response.text)
        self.assertNotIn(str(self.root), response.text)

    def test_dashboard_assets_and_security_headers(self):
        landing = self.client.get("/")
        self.assertIn("Content-Security-Policy", landing.headers)
        self.assertEqual(landing.headers["x-frame-options"], "DENY")
        for asset, snippet in [
            ("/assets/app.css", ".workspace"),
            ("/assets/app.js", "X-Session-Token"),
        ]:
            response = self.client.get(asset)
            self.assertEqual(response.status_code, 200)
            self.assertIn(snippet, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_possible_duplicates_api_is_private_and_read_only(self):
        other = replace(self.job, url='https://example.invalid/jobs/another', source='linkedin')
        with Store(self.db) as store:
            store.add_jobs([other])
            store.stage(other.id)
            store.confirm(other.id, 'fictional-receipt', attested=True)
        path = f'/api/jobs/{self.job.id}/possible-duplicates'
        self.assertEqual(self.call('GET', path, token='bad').status_code, 403)
        response = self.call('GET', path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['jobs'][0]['job_id'], other.id)
        self.assertTrue(response.json()['jobs'][0]['submission_recorded'])
        self.assertNotIn('fictional-receipt', response.text)
        self.assertEqual(self.call('GET', '/api/jobs/0000000000000000/possible-duplicates').status_code, 404)
        with Store(self.db) as store:
            self.assertEqual(len(store.list_jobs()), 2)
            self.assertEqual(store.stage_of(other.id), 'submitted')

    def test_discovery_imports_deduplicated_jobs_without_sending_candidate_data(self):
        new_job = fake_job("https://example.invalid/jobs/new")
        with patch("career_agent.dashboard.discover_isolated", return_value=[self.job, new_job]) as discover:
            response = self.call("POST", "/api/discover", {
                "query": " AI Lead ", "location": " United States ", "results": 5, "acknowledged": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"found": 2, "new": 1, "duplicates": 1})
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(discover.call_args.args[:3], ("AI Lead", "United States", 5))
        self.assertFalse(discover.call_args.args[3].is_set())
        with Store(self.db) as store:
            self.assertEqual(len(store.list_jobs()), 2)
            self.assertEqual(store.stage_of(new_job.id), "discovered")
        rows = self.call("GET", "/api/jobs").json()["jobs"]
        row = next(item for item in rows if item['id'] == new_job.id)
        self.assertEqual(row['discovery_observations'][0]['observation_count'], 1)
        with patch("career_agent.dashboard.discover_isolated", return_value=[new_job, new_job]):
            self.assertEqual(self.call("POST", "/api/discover", {
                "query": "AI Lead", "location": "USA", "acknowledged": True,
            }).status_code, 200)
        rows = self.call("GET", "/api/jobs").json()["jobs"]
        row = next(item for item in rows if item['id'] == new_job.id)
        self.assertEqual(row['discovery_observations'][0]['observation_count'], 2)
        self.assertEqual(row['discovery_observations'][0]['description_comparison'], 'matches')
        changed = replace(new_job, description='New fictional source description')
        with patch('career_agent.dashboard.discover_isolated', return_value=[changed]):
            self.assertEqual(self.call('POST', '/api/discover', {
                'query': 'AI Lead', 'location': 'USA', 'acknowledged': True,
            }).status_code, 200)
        row = next(item for item in self.call('GET', '/api/jobs').json()['jobs'] if item['id'] == new_job.id)
        self.assertEqual(row['discovery_observations'][0]['description_comparison'], 'differs')
        self.assertEqual(row['description'], new_job.description)

    def test_discovery_requires_valid_input_consent_and_session(self):
        valid = {"query": "AI Lead", "location": "United States", "results": 5, "acknowledged": True}
        with patch("career_agent.dashboard.discover_isolated") as discover:
            self.assertEqual(self.call("POST", "/api/discover", valid, token="bad").status_code, 403)
            self.assertEqual(self.call("POST", "/api/discover", valid, headers={"Origin": "https://example.invalid"}).status_code, 403)
            for change in ({"acknowledged": False}, {"acknowledged": "true"}, {"query": " "},
                           {"query": "x" * 201}, {"location": None}, {"results": True},
                           {"results": 0}, {"results": 101}, {"results": 1.5}):
                with self.subTest(change=change):
                    self.assertEqual(self.call("POST", "/api/discover", {**valid, **change}).status_code, 422)
            self.assertEqual(self.call("POST", "/api/discover", []).status_code, 422)
            discover.assert_not_called()

    def test_manual_discovery_reports_sanitized_partial_source_status(self):
        from career_agent.discovery import DiscoveryResults
        statuses = {'indeed': {'state': 'returned', 'returned': 1},
                    'linkedin': {'state': 'reported_error', 'returned': 0}}
        with patch('career_agent.dashboard.discover_isolated', return_value=DiscoveryResults([self.job], statuses)):
            result = self.call('POST', '/api/discover', {'query': 'Lead', 'location': 'USA', 'acknowledged': True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['source_status'], statuses)
        self.assertEqual(result.json()['new'], 0)

    def test_discovery_failure_is_sanitized_and_can_be_retried(self):
        from career_agent.discovery import DiscoveryUnavailable
        from career_agent.automation import DiscoveryStopped, DiscoveryTimedOut
        payload = {"query": "AI Lead", "location": "USA", "acknowledged": True}
        for error, status in ((DiscoveryUnavailable("missing dependency"), 503),
                              (DiscoveryTimedOut("private upstream details"), 504),
                              (DiscoveryStopped("private upstream details"), 503),
                              (RuntimeError("private upstream details"), 502)):
            with patch("career_agent.dashboard.discover_isolated", side_effect=error):
                response = self.call("POST", "/api/discover", payload)
            self.assertEqual(response.status_code, status)
            self.assertNotIn("private upstream details", response.text)
        with patch("career_agent.dashboard.discover_isolated", return_value=[]):
            self.assertEqual(self.call("POST", "/api/discover", payload).json()["new"], 0)
        with Store(self.db) as store:
            self.assertEqual(len(store.list_jobs()), 1)

    def test_discovery_rejects_overlapping_searches_but_keeps_reads_available(self):
        payload = {"query": "AI Lead", "location": "USA", "acknowledged": True}
        def provider(*args):
            self.assertEqual(self.call("GET", "/api/jobs").status_code, 200)
            self.assertEqual(self.call("POST", "/api/discover", payload).status_code, 409)
            return []
        with patch("career_agent.dashboard.discover_isolated", side_effect=provider):
            self.assertEqual(self.call("POST", "/api/discover", payload).status_code, 200)

    def test_pdf_export_requires_exact_current_approved_packet(self):
        self.assertEqual(self.call("GET", f"/api/jobs/{self.job.id}/resume.pdf").status_code, 404)
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/draft", {}).status_code, 200)
        packet = self.call("GET", f"/api/jobs/{self.job.id}/packet").json()["packet"]
        fingerprint = packet["packet_sha256"]
        path = f"/api/jobs/{self.job.id}/resume.pdf?packet_sha256={fingerprint}"
        with patch("career_agent.dashboard.resume_pdf", return_value=b"%PDF-test") as renderer:
            self.assertEqual(self.call("GET", path, token="bad").status_code, 403)
            self.assertEqual(self.call("GET", path).status_code, 412)
            self.call("POST", f"/api/jobs/{self.job.id}/decision", {
                "approved": True, "acknowledged": True, "packet_sha256": fingerprint,
            })
            for invalid in ("", "a" * 64, "é"):
                self.assertEqual(self.call("GET", f"/api/jobs/{self.job.id}/resume.pdf?packet_sha256={invalid}").status_code, 409)
            renderer.assert_not_called()
            response = self.call("GET", path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"%PDF-test")
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertIn(fingerprint[:12], response.headers["content-disposition"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            renderer.assert_called_once_with(packet)
            with patch("career_agent.dashboard.CandidateWorkspace.latest", return_value={"revision": 99}):
                self.assertEqual(self.call("GET", path).status_code, 409)
            self.call("POST", f"/api/jobs/{self.job.id}/decision", {
                "approved": False, "acknowledged": True, "packet_sha256": fingerprint,
            })
            self.assertEqual(self.call("GET", path).status_code, 412)

    def test_pdf_missing_renderer_is_reported_without_changing_approval(self):
        from career_agent.resume_pdf import PDFUnavailable
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/draft", {}).status_code, 200)
        packet = self.call("GET", f"/api/jobs/{self.job.id}/packet").json()["packet"]
        fingerprint = packet["packet_sha256"]
        self.call("POST", f"/api/jobs/{self.job.id}/decision", {
            "approved": True, "acknowledged": True, "packet_sha256": fingerprint,
        })
        with patch("career_agent.dashboard.resume_pdf", side_effect=PDFUnavailable("Install Playwright Chromium")):
            self.assertEqual(self.call("GET", f"/api/jobs/{self.job.id}/resume.pdf?packet_sha256={fingerprint}").status_code, 503)
        with Store(self.db) as store:
            self.assertEqual(store.latest_review(self.job.id)["decision"], "approved_for_manual_submission")
            self.assertNotEqual(store.stage_of(self.job.id), "submitted")

    def test_word_export_requires_exact_current_approval(self):
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.docx').status_code, 404)
        fingerprint = self._draft()
        path = f'/api/jobs/{self.job.id}/resume.docx?packet_sha256={fingerprint}'
        self.assertEqual(self.call('GET', path, token='bad').status_code, 403)
        self.assertEqual(self.call('GET', path).status_code, 412)
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.docx?packet_sha256=' + 'a' * 64).status_code, 409)
        response = self.call('GET', path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'PK'))
        self.assertEqual(response.headers['content-type'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.assertIn(fingerprint[:12], response.headers['content-disposition'])
        with patch('career_agent.dashboard.CandidateWorkspace.latest', return_value={'revision': 99}):
            self.assertEqual(self.call('GET', path).status_code, 409)
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': False, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(self.call('GET', path).status_code, 412)

    def test_no_token_cannot_read_jobs_or_write(self):
        denied = self.client.get("/api/jobs")
        self.assertEqual(denied.status_code, 403)
        invalid = self.call("GET", "/api/jobs", token="not-the-token")
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(
            self.client.post("/api/jobs", json={"company": "X", "title": "Y"}).status_code,
            403,
        )

    def test_host_origin_and_content_type_restrictions(self):
        self.assertEqual(
            self.call("GET", "/api/jobs", headers={"Host": "bad.example"}).status_code,
            400,
        )
        self.assertEqual(
            self.call("POST", "/api/jobs", {}, headers={"Origin": "https://evil.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/jobs", data="company=example",
                headers={"X-Session-Token": self.session, "Content-Type": "application/x-www-form-urlencoded"}
            ).status_code,
            415,
        )

    def test_overview_and_relevance(self):
        jobs = self.call("GET", "/api/jobs")
        self.assertEqual(jobs.status_code, 200)
        entries = jobs.json()["jobs"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["fit_score"], 100)
        self.assertEqual(entries[0]["sponsorship_status"], "posting_claims_sponsorship")
        self.assertEqual(entries[0]["review_decision"], None)
        overview = self.call("GET", "/api/overview").json()
        self.assertEqual(overview["total_jobs"], 1)
        self.assertEqual(overview["submitted_recorded"], 0)
        self.assertFalse(overview["submission_sent_by_application"])

    def test_jobs_pages_cover_old_jobs_despite_concurrent_insert(self):
        with Store(self.db) as store:
            jobs = [fake_job(f'https://example.invalid/jobs/{i}') for i in range(505)]
            store.add_jobs(jobs)
        first = self.call('GET', '/api/jobs?limit=100').json()
        self.assertEqual(len(first['jobs']), 100)
        self.assertIsNotNone(first['next_before'])
        with Store(self.db) as store:
            store.add_jobs([fake_job('https://example.invalid/jobs/new-concurrent')])
        pages, cursor = list(first['jobs']), first['next_before']
        while cursor is not None:
            result = self.call('GET', f'/api/jobs?limit=100&before={cursor}').json()
            pages.extend(result['jobs']); cursor = result['next_before']
        self.assertEqual(len(pages), 506)
        ids = [job['id'] for job in pages]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(self.job.id, ids)
        self.assertEqual(self.call('GET', '/api/jobs?limit=501').status_code, 422)
        self.assertEqual(self.call('GET', '/api/jobs?before=-1').status_code, 422)

    def test_add_job_dedup_and_validation(self):
        value = {
            "company": "Second Company",
            "title": "AI Manager",
            "location": "Boston, MA",
            "job_url": "https://example.invalid/jobs/second",
            "description": "AI governance",
            "is_remote": False,
        }
        created = self.call("POST", "/api/jobs", value)
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["created"])
        repeated = self.call("POST", "/api/jobs", value)
        self.assertFalse(repeated.json()["created"])
        invalid = self.call("POST", "/api/jobs", {**value, "job_url": "javascript:alert(1)"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.call("GET", "/api/jobs").json()["total"], 2)

    def test_requires_configured_verified_facts_to_draft(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app

        other = TestClient(create_app(DashboardConfig(
            db_path=self.db, packet_dir=self.packet_dir,
        )), base_url="http://localhost")
        token = re.search(r'data-session="([^"]+)"', other.get("/").text).group(1)
        denied = other.post(
            f"/api/jobs/{self.job.id}/draft",
            json={}, headers={"X-Session-Token": token},
        )
        self.assertEqual(denied.status_code, 412)
        self.assertFalse((self.packet_dir / f"{self.job.id}.json").exists())

    def _draft(self):
        response = self.call("POST", f"/api/jobs/{self.job.id}/draft", {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["packet_sha256"]

    def test_tailored_draft_uses_only_selected_verified_facts_and_requires_new_review(self):
        data = fake_fact_bank()
        data['facts'].append({**data['facts'][0], 'id': 'experience-02',
            'statement': 'Led a fictional Python migration.', 'source_note': 'Private second source.'})
        self.facts_path.write_text(json.dumps(data))
        options = self.call('GET', '/api/tailoring/facts').json()
        self.assertEqual([f['id'] for f in options['facts']], ['experience-01', 'experience-02'])
        self.assertNotIn('Private', json.dumps(options))
        path = f'/api/jobs/{self.job.id}/tailored-draft'
        for values in ([], ['unknown'], ['experience-01', 'experience-01']):
            self.assertEqual(self.call('POST', path, {'fact_ids': values, 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}).status_code, 422)
        self.assertEqual(self.call('POST', path, {'fact_ids': ['experience-02'], 'workspace_revision': 4, 'facts_sha256': options['facts_sha256']}).status_code, 409)
        self.assertEqual(self.call('POST', path, {'fact_ids': None, 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}).status_code, 422)
        self.assertEqual(self.call('POST', path, {'fact_ids': ['experience-02'], 'workspace_revision': 0, 'facts_sha256': None}).status_code, 422)
        self.assertEqual(self.call('POST', path, {'fact_ids': ['experience-02'], 'workspace_revision': 0, 'facts_sha256': '0' * 64}).status_code, 409)
        response = self.call('POST', path, {'fact_ids': ['experience-02'], 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']})
        self.assertEqual(response.status_code, 200, response.text)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertFalse(packet['stale'])
        self.assertEqual(packet['packet']['selected_fact_ids'], ['experience-02'])
        self.assertIn('Led a fictional Python migration.', packet['packet']['resume_markdown'])
        self.assertNotIn('Delivered a fictional AI program', packet['packet']['resume_markdown'])
        self.assertNotIn('Private second source', json.dumps(packet))
        fingerprint = packet['packet']['packet_sha256']
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
        self.assertEqual(self.call('POST', path, {'fact_ids': ['experience-01'], 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}).status_code, 200)
        replacement = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertNotEqual(replacement['packet']['packet_sha256'], fingerprint)
        self.assertIsNone(replacement['review'])

    def test_tailored_draft_supports_forty_verified_facts_and_new_review(self):
        from career_agent.resume_docx import resume_docx
        from docx import Document
        import io

        data = fake_fact_bank()
        data['facts'] = [{**data['facts'][0], 'id': f'experience-{i:02d}',
                          'statement': f'Fictional achievement number {i:02d}.'}
                         for i in range(1, 42)]
        self.facts_path.write_text(json.dumps(data))
        options = self.call('GET', '/api/tailoring/facts').json()
        self.assertEqual(options['max_facts'], 40)
        ids = [fact['id'] for fact in options['facts']]
        self._draft()
        automatic = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['packet']
        self.assertEqual(len(automatic['selected_fact_ids']), 12)
        payload = {'fact_ids': ids[:40], 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}
        path = f'/api/jobs/{self.job.id}/tailored-draft'
        self.assertEqual(self.call('POST', path, {**payload, 'fact_ids': ids}).status_code, 422)
        result = self.call('POST', path, payload)
        self.assertEqual(result.status_code, 200, result.text)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['packet']
        self.assertEqual(packet['selected_fact_ids'], ids[:40])
        self.assertEqual(packet['omitted_fact_ids'], ids[40:])
        text = '\n'.join(p.text for p in Document(io.BytesIO(resume_docx(packet))).paragraphs)
        self.assertIn('Fictional achievement number 40.', text)
        self.assertNotIn('Fictional achievement number 41.', text)
        approved = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': packet['packet_sha256']})
        self.assertEqual(approved.status_code, 200, approved.text)
        revised = self.call('POST', path, {**payload, 'fact_ids': ids[:39]})
        self.assertEqual(revised.status_code, 200, revised.text)
        replacement = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertNotEqual(replacement['packet']['packet_sha256'], packet['packet_sha256'])
        self.assertIsNone(replacement['review'])

    def test_tailored_layout_reorders_verified_facts_and_requires_new_review(self):
        data = fake_fact_bank()
        data['facts'].extend([
            {**data['facts'][0], 'id': 'experience-02', 'statement': 'A second fictional achievement.'},
            {**data['facts'][0], 'id': 'education-01', 'section': 'education', 'statement': 'A fictional degree.'},
        ])
        self.facts_path.write_text(json.dumps(data))
        options = self.call('GET', '/api/tailoring/facts').json()
        path = f'/api/jobs/{self.job.id}/tailored-draft'
        payload = {'fact_ids': ['experience-02', 'experience-01', 'education-01'],
                   'section_order': ['education', 'experience', 'projects', 'skills', 'certifications'],
                   'workspace_revision': options['workspace_revision'], 'facts_sha256': options['facts_sha256']}
        for invalid in (None, ['education'] * 5, ['education'], ['education', 'experience', 'projects', 'skills', 'made-up']):
            self.assertEqual(self.call('POST', path, {**payload, 'section_order': invalid}).status_code, 422)
        response = self.call('POST', path, payload)
        self.assertEqual(response.status_code, 200, response.text)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['packet']
        self.assertEqual(packet['tailored_section_order'], payload['section_order'])
        self.assertEqual(packet['selected_fact_ids'], ['education-01', 'experience-02', 'experience-01'])
        resume = packet['resume_markdown']
        self.assertLess(resume.index('## Education'), resume.index('## Relevant Experience'))
        self.assertLess(resume.index('A second fictional achievement.'), resume.index(data['facts'][0]['statement']))
        self.assertNotIn('Private', resume)
        first = packet['packet_sha256']
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': first}).status_code, 200)
        response = self.call('POST', path, {**payload, 'section_order': ['experience', 'education', 'projects', 'skills', 'certifications']})
        self.assertEqual(response.status_code, 200, response.text)
        current = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertNotEqual(current['packet']['packet_sha256'], first)
        self.assertIsNone(current['review'])
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': first}).status_code, 409)

    def test_tailored_comparison_snapshot_is_bound_and_never_exported(self):
        from career_agent.application import validate_packet
        from career_agent.resume_pdf import resume_html
        from career_agent.resume_docx import resume_docx
        from docx import Document
        import io

        data = fake_fact_bank()
        data['facts'].append({**data['facts'][0], 'id': 'experience-02',
            'statement': 'Omitted fictional statement for comparison.', 'source_note': 'Private comparison evidence.'})
        self.facts_path.write_text(json.dumps(data))
        options = self.call('GET', '/api/tailoring/facts').json()
        request = {'fact_ids': ['experience-01'], 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}
        response = self.call('POST', f'/api/jobs/{self.job.id}/tailored-draft', request)
        self.assertEqual(response.status_code, 200, response.text)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['packet']
        self.assertEqual([item['id'] for item in packet['baseline_fact_evidence']], ['experience-01', 'experience-02'])
        self.assertEqual(packet['baseline_fact_evidence'][1]['statement'], 'Omitted fictional statement for comparison.')
        self.assertNotIn('Private comparison evidence.', json.dumps(packet))
        altered = json.loads(json.dumps(packet)); altered['baseline_fact_evidence'][1]['statement'] += ' altered'
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_packet(altered)
        self.assertNotIn('Omitted fictional statement', packet['cover_note'])
        self.assertNotIn('Omitted fictional statement', resume_html(packet))
        self.assertNotIn('Private comparison evidence.', resume_html(packet))
        document = Document(io.BytesIO(resume_docx(packet)))
        rendered = '\n'.join(paragraph.text for paragraph in document.paragraphs)
        self.assertNotIn('Omitted fictional statement', rendered)
        self.assertNotIn('Private comparison evidence.', rendered)
        self.assertNotIn('Omitted fictional statement', self.call('GET', '/api/crm/export').text)

        # A legacy or default packet remains valid without the new optional field.
        default = self.call('POST', f'/api/jobs/{self.job.id}/draft', {})
        self.assertEqual(default.status_code, 200, default.text)
        legacy = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['packet']
        self.assertNotIn('baseline_fact_evidence', legacy)
        validate_packet(legacy)

    def test_tailored_packet_survives_restart_and_rejects_changed_job(self):
        from fastapi.testclient import TestClient
        from career_agent.dashboard import DashboardConfig, create_app
        options = self.call('GET', '/api/tailoring/facts').json()
        path = f'/api/jobs/{self.job.id}/tailored-draft'
        selection = {'fact_ids': ['experience-01'], 'workspace_revision': 0, 'facts_sha256': options['facts_sha256']}
        fingerprint = self.call('POST', path, selection).json()['packet_sha256']
        decision = {'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', decision).status_code, 200)
        self.client = TestClient(create_app(DashboardConfig(db_path=self.db, packet_dir=self.packet_dir,
            profile_path=self.profile_path, facts_path=self.facts_path)), base_url='http://localhost')
        self.session = re.search(r'data-session="([^"]+)"', self.client.get('/').text).group(1)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet').json()
        self.assertFalse(packet['stale'])
        self.assertEqual(packet['review']['decision'], 'approved_for_manual_submission')
        for extension, target in (('pdf', 'resume_pdf'), ('docx', 'resume_docx')):
            with patch('career_agent.dashboard.' + target, return_value=b'fictional'):
                self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.{extension}?packet_sha256={fingerprint}').status_code, 200)
        self.assertEqual(self.call('PUT', f'/api/jobs/{self.job.id}/description', {
            'description': 'Corrected fictional role.', 'expected_revision': 0, 'acknowledged': True}).status_code, 200)
        self.assertTrue(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.pdf?packet_sha256={fingerprint}').status_code, 409)
        fingerprint = self.call('POST', path, selection).json()['packet_sha256']
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {**decision, 'packet_sha256': fingerprint}).status_code, 200)
        submitted = self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
            'packet_sha256': fingerprint, 'receipt': 'fictional receipt', 'attest_confirmed': True})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(self.call('POST', path, selection).status_code, 409)

    def test_tailored_packet_stales_on_profile_correction(self):
        self.assertEqual(self.call('POST', '/api/workspace/import', {'acknowledged': True}).status_code, 200)
        options = self.call('GET', '/api/tailoring/facts').json()
        path = f'/api/jobs/{self.job.id}/tailored-draft'
        selection = {'fact_ids': ['experience-01'], 'workspace_revision': options['workspace_revision'], 'facts_sha256': options['facts_sha256']}
        fingerprint = self.call('POST', path, selection).json()['packet_sha256']
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
        workspace = self.call('GET', '/api/workspace').json()['workspace']
        workspace['profile']['headline'] = 'Corrected fictional headline'
        self.assertEqual(self.call('PUT', '/api/workspace', {'expected_revision': workspace['revision'],
            'profile': workspace['profile'], 'facts': workspace['facts']}).status_code, 200)
        self.assertTrue(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])
        self.assertEqual(self.call('POST', path, selection).status_code, 409)
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.docx?packet_sha256={fingerprint}').status_code, 409)

    def test_draft_review_and_manual_submission_workflow(self):
        job_id = self.job.id
        draft_id = self._draft()
        packet_resp = self.call("GET", f"/api/jobs/{job_id}/packet")
        self.assertEqual(packet_resp.status_code, 200)
        packet = packet_resp.json()["packet"]
        self.assertEqual(packet["packet_sha256"], draft_id)
        self.assertIn("Delivered a fictional AI program", packet["resume_markdown"])
        self.assertNotIn("Private fictional evidence", json.dumps(packet_resp.json()))
        self.assertFalse(packet["submission_sent"])
        self.assertEqual(load_packet(self.packet_dir / f"{job_id}.json")["packet_sha256"], draft_id)

        unapproved = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id,
            "receipt": "employer-receipt-123",
            "attest_confirmed": True,
        })
        self.assertEqual(unapproved.status_code, 412)
        missing_attestation = self.call("POST", f"/api/jobs/{job_id}/decision", {
            "packet_sha256": draft_id,
            "approved": True,
        })
        self.assertEqual(missing_attestation.status_code, 422)
        approved = self.call("POST", f"/api/jobs/{job_id}/decision", {
            "packet_sha256": draft_id,
            "approved": True,
            "acknowledged": True,
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["decision"], "approved_for_manual_submission")
        self.assertFalse(approved.json()["submission_sent"])
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(job_id), "discovered")
            self.assertEqual(store.latest_review(job_id, draft_id)["decision"], "approved_for_manual_submission")

        reviewed = self.call("GET", f"/api/jobs/{job_id}/packet").json()
        self.assertEqual(reviewed["review"]["decision"], "approved_for_manual_submission")
        events = self.call("GET", "/api/reviews").json()["reviews"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("Private fictional evidence", json.dumps(events))

        no_receipt = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id, "receipt": "", "attest_confirmed": True,
        })
        self.assertEqual(no_receipt.status_code, 422)
        submitted = self.call("POST", f"/api/jobs/{job_id}/submitted", {
            "packet_sha256": draft_id,
            "receipt": "employer-receipt-123",
            "attest_confirmed": True,
        })
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertFalse(submitted.json()["submission_sent_by_application"])
        self.assertEqual(submitted.json()["application_stage"], "submitted")
        self.assertEqual(self.call("GET", "/api/overview").json()["submitted_recorded"], 1)
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(job_id), "submitted")
        submitted_packet_path = self.packet_dir / f"{job_id}.json"
        original_contents = submitted_packet_path.read_bytes()
        updated_facts = fake_fact_bank()
        updated_facts["facts"][0]["statement"] = "Delivered a fictional revised cloud automation program."
        self.facts_path.write_text(json.dumps(updated_facts))
        locked = self.call("POST", f"/api/jobs/{job_id}/draft", {})
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(submitted_packet_path.read_bytes(), original_contents)
        from contextlib import redirect_stderr
        from io import StringIO
        from career_agent.cli import main
        with redirect_stderr(StringIO()):
            self.assertEqual(main([
                "--db", str(self.db), "draft", job_id,
                "--profile", str(self.profile_path), "--facts", str(self.facts_path),
                "--output", str(submitted_packet_path),
            ]), 1)
        self.assertEqual(submitted_packet_path.read_bytes(), original_contents)
        self.assertEqual(self.call("POST", f"/api/jobs/{job_id}/decision", {
            "approved": False, "acknowledged": True, "packet_sha256": draft_id,
        }).status_code, 422)

    def test_old_approval_not_valid_for_regenerated_packet(self):
        old_hash = self._draft()
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/decision", {
            "approved": True, "acknowledged": True, "packet_sha256": old_hash,
        }).status_code, 200)
        modified = fake_fact_bank()
        modified["facts"][0]["statement"] = "Delivered a fictional revised AWS program with independent review."
        self.facts_path.write_text(json.dumps(modified))
        new_hash = self._draft()
        self.assertNotEqual(old_hash, new_hash)
        self.assertIsNone(self.call("GET", f"/api/jobs/{self.job.id}/packet").json()["review"])
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/decision", {
            "approved": True, "acknowledged": True, "packet_sha256": old_hash,
        }).status_code, 409)
        self.assertEqual(self.call("POST", f"/api/jobs/{self.job.id}/submitted", {
            "packet_sha256": new_hash, "receipt": "actual-receipt",
            "attest_confirmed": True,
        }).status_code, 412)

    def test_bad_job_ids_and_bad_payloads(self):
        self.assertEqual(self.call("GET", "/api/jobs/../../private/packet").status_code, 404)
        self.assertEqual(self.call("GET", "/api/jobs/not-an-id/packet").status_code, 404)
        self.assertEqual(self.call("POST", "/api/jobs", ["array-not-object"]).status_code, 422)
        self.assertEqual(self.call("POST", "/api/jobs", {"company": "No URL", "title": "Missing link"}).status_code, 422)

    def test_bounded_json_body_and_forged_session_are_rejected(self):
        large = {"company": "Example", "title": "Too big", "job_url": "https://example.invalid/large", "description": "X" * (129 * 1024)}
        response = self.call("POST", "/api/jobs", large)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.call("GET", "/api/jobs", token="another-process-token").status_code, 403)

    def test_local_job_untrusted_html_is_only_json_data(self):
        hostile = {
            "company": "<img src=x onerror=alert(1)>",
            "title": "<script>alert('injected')</script>",
            "job_url": "https://example.invalid/jobs/hostile",
            "description": "A <b>markup</b> job",
        }
        created = self.call("POST", "/api/jobs", hostile)
        self.assertEqual(created.status_code, 201)
        response = self.call("GET", "/api/jobs")
        self.assertEqual(response.status_code, 200)
        job = next(j for j in response.json()["jobs"] if j["id"] == created.json()["job_id"])
        self.assertEqual(job["title"], hostile["title"])
        self.assertNotIn(hostile["title"], self.client.get("/").text)
        # Frontend uses textContent; untrusted strings are never interpreted as HTML.
        source = self.client.get("/assets/app.js").text
        self.assertIn("textContent", source)
        self.assertNotRegex(source, r"\b(?:innerHTML|outerHTML)\s*=")

    def test_description_correction_invalidates_even_if_restored_and_locks_after_submission(self):
        path = f'/api/jobs/{self.job.id}/description'
        fingerprint = self._draft()
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
        update = {'description': 'This role does not offer visa sponsorship.', 'expected_revision': 0,
                  'source_note': 'Fictional employer posting reviewed', 'acknowledged': True}
        self.assertEqual(self.call('PUT', path, {**update, 'acknowledged': False}).status_code, 422)
        self.assertEqual(self.call('PUT', path, update).json(), {'revision': 1, 'changed': True})
        self.assertEqual(self.call('PUT', path, update).status_code, 409)
        board = next(j for j in self.call('GET', '/api/jobs').json()['jobs'] if j['id'] == self.job.id)
        self.assertEqual(board['sponsorship_status'], 'explicit_exclusion')
        self.assertEqual(board['description_revision'], 1)
        self.assertTrue(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 409)
        history = self.call('GET', f'/api/jobs/{self.job.id}/description-history').json()['revisions']
        self.assertEqual(history[0]['previous_description'], self.job.description)
        self.assertEqual(history[0]['source_note'], update['source_note'])
        self.assertEqual(self.call('PUT', path, {**update, 'description': self.job.description, 'expected_revision': 1}).json()['revision'], 2)
        self.assertTrue(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])
        newer = self._draft()
        self.assertNotEqual(fingerprint, newer)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': newer}).status_code, 200)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
            'packet_sha256': newer, 'receipt': 'fictional-receipt', 'attest_confirmed': True}).status_code, 200)
        self.assertEqual(self.call('PUT', path, {**update, 'expected_revision': 2}).status_code, 422)
        self.assertFalse(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])

    def test_description_cannot_change_during_submission_transaction(self):
        from threading import Event, Thread
        fingerprint = self._draft()
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        entered, release, corrected = Event(), Event(), Event()
        original = Store.latest_review
        responses = {}
        def delayed(store, *args, **kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('Timed out waiting for concurrent request')
            return original(store, *args, **kwargs)
        def submit():
            responses['submitted'] = self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
                'packet_sha256': fingerprint, 'receipt': 'fictional-123', 'attest_confirmed': True})
        def correct():
            responses['corrected'] = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                'description': 'New fictional description.', 'expected_revision': 0, 'acknowledged': True})
            corrected.set()
        with patch.object(Store, 'latest_review', delayed):
            thread1 = Thread(target=submit); thread1.start()
            self.assertTrue(entered.wait(5))
            thread2 = Thread(target=correct); thread2.start()
            self.assertFalse(corrected.wait(.2), 'Correction must wait while submission commits')
            release.set(); thread1.join(10); thread2.join(10)
        self.assertEqual(responses['submitted'].status_code, 200)
        self.assertEqual(responses['corrected'].status_code, 422)
        with Store(self.db) as store:
            self.assertEqual(store.get_job(self.job.id).description, self.job.description)

    def test_job_page_reads_description_and_revision_from_one_snapshot(self):
        from threading import Event, Thread
        with Store(self.db) as store:
            store.connection.execute('PRAGMA journal_mode=WAL')
        entered, release = Event(), Event()
        original = Store.job_page
        results = {}
        def delayed(store, *args, **kwargs):
            page = original(store, *args, **kwargs)
            entered.set()
            if not release.wait(5): raise RuntimeError('Timed out waiting for correction')
            return page
        def read(): results['page'] = self.call('GET', '/api/jobs')
        with patch.object(Store, 'job_page', delayed):
            thread = Thread(target=read); thread.start()
            self.assertTrue(entered.wait(5))
            try:
                result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                    'description': 'Corrected fictional posting.', 'expected_revision': 0, 'acknowledged': True})
                self.assertEqual(result.status_code, 200)
            finally:
                release.set(); thread.join(10)
        item = results['page'].json()['jobs'][0]
        self.assertEqual(item['description'], self.job.description)
        self.assertEqual(item['description_revision'], 0)
        stale = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
            'description': item['description'], 'expected_revision': item['description_revision'], 'acknowledged': True})
        self.assertEqual(stale.status_code, 409)

    def test_draft_does_not_publish_old_description_as_new_revision(self):
        from threading import Event, Thread
        import career_agent.dashboard as dashboard
        with Store(self.db) as store:
            store.connection.execute('PRAGMA journal_mode=WAL')
        entered, release = Event(), Event()
        original = dashboard._get_job
        results, first = {}, [True]
        def delayed(store, job_id):
            job = original(store, job_id)
            if first[0]:
                first[0] = False; entered.set()
                if not release.wait(5): raise RuntimeError('Timed out waiting for correction')
            return job
        def draft(): results['draft'] = self.call('POST', f'/api/jobs/{self.job.id}/draft', {})
        with patch('career_agent.dashboard._get_job', side_effect=delayed):
            thread = Thread(target=draft); thread.start()
            self.assertTrue(entered.wait(5))
            try:
                result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                    'description': 'Corrected fictional posting.', 'expected_revision': 0, 'acknowledged': True})
                self.assertEqual(result.status_code, 200)
            finally:
                release.set(); thread.join(10)
        self.assertEqual(results['draft'].status_code, 422)
        self.assertFalse((self.packet_dir / f'{self.job.id}.json').exists())

    def test_submission_winning_before_new_run_does_not_supersede_reviewed_packet(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(response.status_code, 200, response.text)
        original = Workflows.start
        submissions = []
        def submitted_before_start(manager, *args, **kwargs):
            submissions.append(self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
                'packet_sha256': fingerprint, 'receipt': 'fictional-receipt', 'attest_confirmed': True}))
            return original(manager, *args, **kwargs)
        with patch.object(Workflows, 'start', submitted_before_start):
            rejected = self.call('POST', f'/api/jobs/{self.job.id}/draft', {})
        self.assertEqual(submissions[0].status_code, 200, submissions[0].text)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        with Store(self.db) as store:
            self.assertEqual(store.stage_of(self.job.id), 'submitted')
            runs = store.connection.execute('SELECT status,packet_sha256 FROM workflow_runs WHERE job_id=?', (self.job.id,)).fetchall()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]['packet_sha256'], fingerprint)
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet')
        self.assertEqual(packet.status_code, 200, packet.text)
        self.assertFalse(packet.json()['stale'])
        self.assertEqual(packet.json()['review']['decision'], 'approved_for_manual_submission')

    def test_cli_draft_and_description_correction_are_serialized(self):
        from threading import Event, Thread
        from career_agent.cli import main
        from career_agent.application import save_packet
        entered, release, corrected = Event(), Event(), Event()
        result = {}
        def delayed(packet, path):
            entered.set()
            if not release.wait(5): raise RuntimeError('Timed out waiting for correction')
            return save_packet(packet, path)
        def draft():
            result['draft'] = main(['--db', str(self.db), 'draft', self.job.id,
                '--profile', str(self.profile_path), '--facts', str(self.facts_path),
                '--output', str(self.packet_dir / f'{self.job.id}.json')])
        def correct():
            result['correction'] = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                'description': 'Corrected fictional posting.', 'expected_revision': 0, 'acknowledged': True})
            corrected.set()
        with patch('career_agent.cli.save_packet', side_effect=delayed):
            thread1 = Thread(target=draft); thread1.start()
            self.assertTrue(entered.wait(5))
            thread2 = Thread(target=correct); thread2.start()
            self.assertFalse(corrected.wait(.2))
            release.set(); thread1.join(10); thread2.join(10)
        self.assertEqual(result['draft'], 0)
        self.assertEqual(result['correction'].status_code, 200)
        self.assertTrue(self.call('GET', f'/api/jobs/{self.job.id}/packet').json()['stale'])

    def test_exports_recheck_legacy_files_after_rendering(self):
        for extension, target, changed_file in (('pdf', 'resume_pdf', 'facts'),
                                                ('docx', 'resume_docx', 'profile'),
                                                ('docx', 'resume_docx', 'missing_facts')):
            with self.subTest(extension=extension, changed_file=changed_file):
                self.facts_path.write_text(json.dumps(fake_fact_bank()))
                from dataclasses import asdict
                self.profile_path.write_text(json.dumps(asdict(CandidateProfile(
                    target_titles=('AI Program Lead',), skills=('Python',), preferred_locations=(), allow_remote=True))))
                fingerprint = self._draft()
                self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                    'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
                def render(_packet):
                    if changed_file == 'missing_facts':
                        self.facts_path.unlink()
                    elif changed_file == 'facts':
                        changed = fake_fact_bank(); changed['facts'][0]['statement'] += ' Changed.'
                        self.facts_path.write_text(json.dumps(changed))
                    else:
                        self.profile_path.write_text(json.dumps({'target_titles': ['Different role']}))
                    return b'fictional-document'
                with patch('career_agent.dashboard.' + target, side_effect=render):
                    response = self.call('GET', f'/api/jobs/{self.job.id}/resume.{extension}?packet_sha256={fingerprint}')
                self.assertEqual(response.status_code, 409, response.text)

    def test_submission_rechecks_legacy_facts_inside_transaction(self):
        fingerprint = self._draft()
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
        original = Store.workspace_revision
        def changed(store):
            changed = fake_fact_bank(); changed['facts'][0]['statement'] += ' Changed.'
            self.facts_path.write_text(json.dumps(changed))
            return original(store)
        with patch.object(Store, 'workspace_revision', changed):
            response = self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
                'packet_sha256': fingerprint, 'receipt': 'fictional-receipt', 'attest_confirmed': True})
        self.assertEqual(response.status_code, 409, response.text)
        with Store(self.db) as store:
            self.assertNotEqual(store.stage_of(self.job.id), 'submitted')

    def test_missing_legacy_facts_fail_closed_before_export_or_review(self):
        fingerprint = self._draft()
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
        self.facts_path.unlink()
        packet = self.call('GET', f'/api/jobs/{self.job.id}/packet')
        self.assertEqual(packet.status_code, 200, packet.text)
        self.assertTrue(packet.json()['stale'])
        self.assertEqual(self.call('GET', f'/api/jobs/{self.job.id}/resume.docx?packet_sha256={fingerprint}').status_code, 409)
        self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 409)

    def test_workflow_review_rechecks_legacy_facts_inside_transaction(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        original = Workflows.advance
        def advance(manager, run_id, signature, decision=None, **kwargs):
            if decision is not None:
                changed = fake_fact_bank(); changed['facts'][0]['statement'] += ' Changed.'
                self.facts_path.write_text(json.dumps(changed))
            return original(manager, run_id, signature, decision, **kwargs)
        with patch.object(Workflows, 'advance', advance):
            response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertIn(response.status_code, (409, 422), response.text)
        self.assertEqual(self.call('GET', '/api/reviews').json()['reviews'], [])

    def test_workflow_review_handles_file_disappearing_after_stale_check(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        original = Workflows.review
        def review(manager, *args, **kwargs):
            self.facts_path.unlink()
            return original(manager, *args, **kwargs)
        with patch.object(Workflows, 'review', review):
            response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn(str(self.facts_path), response.text)
        self.assertEqual(self.call('GET', '/api/reviews').json()['reviews'], [])

    def test_pdf_export_rechecks_description_after_rendering(self):
        fingerprint = self._draft()
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        def render(_packet):
            result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                'description': 'Corrected fictional role.', 'expected_revision': 0, 'acknowledged': True})
            self.assertEqual(result.status_code, 200)
            return b'fictional-pdf'
        with patch('career_agent.dashboard.resume_pdf', side_effect=render):
            response = self.call('GET', f'/api/jobs/{self.job.id}/resume.pdf?packet_sha256={fingerprint}')
        self.assertEqual(response.status_code, 409)

    def test_exports_recheck_workspace_revision_after_rendering(self):
        imported = self.call('POST', '/api/workspace/import', {'acknowledged': True})
        self.assertEqual(imported.status_code, 200)
        for extension, target in (('pdf', 'resume_pdf'), ('docx', 'resume_docx')):
            with self.subTest(extension=extension):
                fingerprint = self._draft()
                self.assertEqual(self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                    'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint}).status_code, 200)
                def render(_packet):
                    workspace = self.call('GET', '/api/workspace').json()['workspace']
                    workspace['profile']['headline'] += ' changed'
                    result = self.call('PUT', '/api/workspace', {'expected_revision': workspace['revision'],
                        'profile': workspace['profile'], 'facts': workspace['facts']})
                    self.assertEqual(result.status_code, 200)
                    return b'fictional-document'
                with patch('career_agent.dashboard.' + target, side_effect=render):
                    response = self.call('GET', f'/api/jobs/{self.job.id}/resume.{extension}?packet_sha256={fingerprint}')
                self.assertEqual(response.status_code, 409)

    def test_legacy_review_rechecks_workspace_under_lock(self):
        from career_agent.application import build_packet, save_packet
        from career_agent.candidate import FactBank
        packet = build_packet(self.job, CandidateProfile.load(self.profile_path), FactBank.parse(fake_fact_bank()))
        save_packet(packet, self.packet_dir / (self.job.id + '.json'))
        original = Store.record_review
        def record(store, *args, **kwargs):
            response = self.call('POST', '/api/workspace/import', {'acknowledged': True})
            self.assertEqual(response.status_code, 200)
            return original(store, *args, **kwargs)
        with patch.object(Store, 'record_review', record):
            response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                'approved': True, 'acknowledged': True, 'packet_sha256': packet['packet_sha256']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.call('GET', '/api/reviews').json()['reviews'], [])

    def test_workspace_cannot_change_during_submission_transaction(self):
        from threading import Event, Thread
        self.assertEqual(self.call('POST', '/api/workspace/import', {'acknowledged': True}).status_code, 200)
        fingerprint = self._draft()
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        workspace = self.call('GET', '/api/workspace').json()['workspace']
        workspace['profile']['headline'] += ' changed'
        entered, release, changed = Event(), Event(), Event()
        original, responses = Store.latest_review, {}
        def delayed(store, *args, **kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('Timed out waiting for concurrent request')
            return original(store, *args, **kwargs)
        def submit():
            responses['submitted'] = self.call('POST', f'/api/jobs/{self.job.id}/submitted', {
                'packet_sha256': fingerprint, 'receipt': 'fictional-123', 'attest_confirmed': True})
        def update():
            responses['updated'] = self.call('PUT', '/api/workspace', {
                'expected_revision': workspace['revision'], 'profile': workspace['profile'], 'facts': workspace['facts']})
            changed.set()
        with patch.object(Store, 'latest_review', delayed):
            thread1 = Thread(target=submit); thread1.start()
            self.assertTrue(entered.wait(5))
            thread2 = Thread(target=update); thread2.start()
            self.assertFalse(changed.wait(.2), 'Profile update must wait for submission to commit')
            release.set(); thread1.join(10); thread2.join(10)
        self.assertEqual(responses['submitted'].status_code, 200)
        self.assertEqual(responses['updated'].status_code, 200)

    def test_workflow_review_rechecks_workspace_under_lock(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        original = Workflows.advance
        def advance(manager, run_id, signature, decision=None, **kwargs):
            if decision is not None:
                response = self.call('POST', '/api/workspace/import', {'acknowledged': True})
                self.assertEqual(response.status_code, 200)
            return original(manager, run_id, signature, decision, **kwargs)
        with patch.object(Workflows, 'advance', advance):
            response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.call('GET', '/api/reviews').json()['reviews'], [])

    def test_cli_external_workflow_remains_available_with_initialized_workspace(self):
        from career_agent.cli import main
        self.assertEqual(self.call('POST', '/api/workspace/import', {'acknowledged': True}).status_code, 200)
        with patch('builtins.input', return_value=''):
            code = main(['--db', str(self.db), 'workflow', self.job.id,
                '--profile', str(self.profile_path), '--facts', str(self.facts_path),
                '--output', str(self.packet_dir / f'{self.job.id}.json')])
        self.assertEqual(code, 0)

    def test_word_export_rechecks_description_after_rendering(self):
        fingerprint = self._draft()
        self.call('POST', f'/api/jobs/{self.job.id}/decision', {
            'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        def render(_packet):
            result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                'description': 'Corrected fictional role.', 'expected_revision': 0, 'acknowledged': True})
            self.assertEqual(result.status_code, 200)
            return b'fictional-docx'
        with patch('career_agent.dashboard.resume_docx', side_effect=render):
            response = self.call('GET', f'/api/jobs/{self.job.id}/resume.docx?packet_sha256={fingerprint}')
        self.assertEqual(response.status_code, 409)

    def test_workflow_review_rechecks_description_under_lock(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        original = Workflows.advance
        def advance(manager, run_id, signature, decision=None, **kwargs):
            if decision is not None:
                result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                    'description': 'Corrected before approval.', 'expected_revision': 0, 'acknowledged': True})
                self.assertEqual(result.status_code, 200)
            return original(manager, run_id, signature, decision, **kwargs)
        with patch.object(Workflows, 'advance', advance):
            response = self.call('POST', f'/api/jobs/{self.job.id}/decision', {
                'approved': True, 'acknowledged': True, 'packet_sha256': fingerprint})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.call('GET', '/api/reviews').json()['reviews'], [])

    def test_no_candidate_claims_embedded_in_homepage(self):
        self.assertNotIn("Fictional Candidate", self.client.get("/").text)
        self.assertNotIn("Delivered a fictional AI program", self.client.get("/").text)


class ServerBindingTests(unittest.TestCase):
    @unittest.skipUnless(HAS_WEB, 'Install test dependencies with pip install -e ".[web,dev]"')
    def test_loopback_only_binding_and_port_limits(self):
        from unittest.mock import patch
        from career_agent.dashboard import DashboardConfig, serve
        with patch("uvicorn.run") as run:
            serve(DashboardConfig(), port=8765)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 8765)
            self.assertFalse(run.call_args.kwargs["access_log"])
        with self.assertRaises(ValueError):
            serve(DashboardConfig(), port=443)


class ReviewStoreTests(unittest.TestCase):
    def test_review_history_is_append_only_and_packet_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "db.sqlite3") as store:
                job = fake_job()
                store.add_jobs([job])
                a = "a" * 64
                b = "b" * 64
                store.record_review(job.id, a, approved=True)
                store.record_review(job.id, a, approved=False)
                store.record_review(job.id, b, approved=True)
                self.assertEqual(len(store.review_history()), 3)
                self.assertEqual(store.latest_review(job.id, a)["decision"], "rejected_by_reviewer")
                self.assertEqual(store.latest_review(job.id, b)["decision"], "approved_for_manual_submission")
                self.assertIsNone(store.latest_review(job.id, "c" * 64))

    def test_non_boolean_review_and_invalid_sha_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "db.sqlite3") as store:
                job = fake_job()
                store.add_jobs([job])
                with self.assertRaises(ValueError):
                    store.record_review(job.id, "a" * 64, approved="true")
                with self.assertRaises(ValueError):
                    store.record_review(job.id, "invalid", approved=True)


if __name__ == "__main__":
    unittest.main()
