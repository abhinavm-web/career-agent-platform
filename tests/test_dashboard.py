"""Offline FastAPI workspace tests with a fictional candidate and no network calls."""
from __future__ import annotations

import importlib.util
import base64
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from pathlib import Path

from career_agent.application import load_packet
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

    def test_resume_extract_is_local_and_does_not_change_workspace(self):
        before = self.call('GET', '/api/workspace').json()
        payload = {'filename': 'fictional.txt', 'content': base64.b64encode(b'Fictional experience').decode()}
        self.assertEqual(self.call('POST', '/api/workspace/resume-extract', payload, token='bad').status_code, 403)
        response = self.call('POST', '/api/workspace/resume-extract', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['text'], 'Fictional experience')
        self.assertEqual(self.call('GET', '/api/workspace').json(), before)
        self.assertEqual(self.call('POST', '/api/workspace/resume-extract', {'filename': 'x.pdf', 'content': '!!!!'}).status_code, 422)

    def test_ai_analysis_requires_consent_and_preserves_application_state(self):
        route = {'local': {'provider': 'ollama', 'model': 'fictional', 'base_url': 'http://127.0.0.1:11434/v1'}}
        with patch('career_agent.ai.routes', return_value=route), patch('career_agent.ai.complete', return_value='{"requirements":[]}') as complete:
            result = self.call('GET', '/api/ai/routes')
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('key_env', result.text)
            url = f'/api/jobs/{self.job.id}/ai-analysis'
            self.assertEqual(self.call('POST', url, {'route': 'local'}).status_code, 422)
            complete.assert_not_called()
            self.assertEqual(self.call('POST', url, {'route': 'local', 'acknowledged': True}).status_code, 200)
            with patch('career_agent.ai.complete', side_effect=ValueError('Provider unavailable')):
                self.assertEqual(self.call('POST', url, {'route': 'local', 'acknowledged': True}).status_code, 422)
            self.assertEqual(self.call('POST', url, {'route': 'local', 'acknowledged': True}).status_code, 200)
            self.assertFalse(self.packet_dir.exists())

    def test_profile_chat_sends_only_opted_in_verified_statements(self):
        path = '/api/workspace/chat'
        request = {'route': 'local', 'message': 'What should I highlight?'}
        with patch('career_agent.dashboard.ai.profile_chat', return_value={
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

    def test_workflow_review_rechecks_description_under_lock(self):
        from career_agent.workflows import Workflows
        fingerprint = self._draft()
        original = Workflows.advance
        def advance(manager, run_id, signature, decision=None):
            if decision is not None:
                result = self.call('PUT', f'/api/jobs/{self.job.id}/description', {
                    'description': 'Corrected before approval.', 'expected_revision': 0, 'acknowledged': True})
                self.assertEqual(result.status_code, 200)
            return original(manager, run_id, signature, decision)
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
