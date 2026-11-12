"""Local-only dashboard with explicitly requested JobSpy discovery; no submissions.

Threat model: a single trusted user at 127.0.0.1. This is NOT a hosted SaaS.
A random per-process session token protects API reads and writes; host and Origin
are checked to reduce cross-origin and DNS rebinding risks.
"""
from __future__ import annotations

import hmac
import hashlib
import html
import json
import io
import csv
import re
import secrets
import tempfile
from threading import Lock
from dataclasses import asdict, dataclass
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .application import MAX_TAILORED_FACTS, _reject_symlink_components, load_packet
from .candidate import FactBank, SECTIONS
from . import ai
from .ai_history import AIHistory, DailyRequestLimit, run_recorded
from .automation import Automation, DiscoveryStopped, DiscoveryTimedOut
from .workflows import Workflows, inputs as workflow_inputs, signature as workflow_signature
from .crm import ApplicationCRM, StageConflict
from .discovery import DiscoveryUnavailable, discover_isolated, validate_sites
from .models import CandidateProfile, Job
from .h1b import H1BResearch, validate_lca_source
from .uscis import USCISRecords
from .posting_checks import PostingChecks, check_posting, target as posting_target
from .resume_pdf import PDFUnavailable, resume_pdf
from .resume_docx import DOCXUnavailable, resume_docx
from .scoring import constraint_evidence, score_job, title_level_evidence
from .sponsor_evidence import load_filing_evidence, sponsorship_report
from .storage import Store, DescriptionConflict
from .workspace import CandidateWorkspace, StaleRevision

JOB_ID = re.compile(r"[a-f0-9]{16}\Z")
API_BODY_LIMIT = 128 * 1024
# Full workspace saves can contain 100 validated statements and private notes.
WORKSPACE_BODY_LIMIT = 2 * 1024 * 1024
LCA_WORKBOOK_LIMIT = 512 * 1024 * 1024


@dataclass(frozen=True)
class DashboardConfig:
    db_path: Path = Path(".local/career_agent.sqlite3")
    packet_dir: Path = Path(".local/packets")
    profile_path: Path | None = None
    facts_path: Path | None = None
    filings_path: Path | None = None


def _id(value: str) -> str:
    if not JOB_ID.fullmatch(value):
        raise HTTPException(status_code=404, detail="Job not found")
    return value


def _get_job(store: Store, job_id: str) -> Job:
    _id(job_id)
    try:
        return store.get_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Job not found") from error


def create_app(config: DashboardConfig) -> FastAPI:
    """Create local web app. Profile and fact bank are loaded only when needed."""
    ai_lock = Lock()
    automation = Automation(config.db_path)
    workflows = Workflows(config.db_path, config.packet_dir)
    @asynccontextmanager
    async def lifespan(app):
        automation.start()
        try:
            yield
        finally:
            await run_in_threadpool(automation.close)
    app = FastAPI(
        lifespan=lifespan,
        title="Career Agent Local Workspace",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]"],
    )
    token = secrets.token_urlsafe(32)
    discovery_lock = Lock()
    h1b_import_lock = Lock()
    h1b_progress_lock = Lock()
    h1b_progress = {'phase': 'idle', 'amount': 0}

    def set_h1b_progress(phase, amount):
        with h1b_progress_lock:
            h1b_progress.update(phase=phase, amount=amount)
    live_check_lock = Lock()

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-session-token", "")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse({"detail": "Session token required"}, status_code=403)
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin != expected_origin:
                return JSONResponse({"detail": "Cross-origin request denied"}, status_code=403)
            if request.method not in {"GET", "HEAD"}:
                workbook_upload = request.method == 'POST' and request.url.path == '/api/h1b/import-workbook'
                if workbook_upload:
                    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
                        return JSONResponse({'detail': 'Use an XLSX file upload'}, status_code=415)
                    # The endpoint streams this bounded body directly to a temporary file.
                else:
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        return JSONResponse({"detail": "Use application/json"}, status_code=415)
                    limit = (
                        10 * 1024 * 1024 if request.url.path in {"/api/h1b/import", "/api/h1b/uscis/import"} else
                        3 * 1024 * 1024 if request.url.path == "/api/workspace/resume-extract" else
                        WORKSPACE_BODY_LIMIT
                        if request.url.path == "/api/workspace" and request.method == "PUT"
                        else API_BODY_LIMIT
                    )
                    chunks = []
                    size = 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            return JSONResponse({"detail": "Request too large"}, status_code=413)
                        chunks.append(chunk)
                    # Starlette replays the cached body to the endpoint after middleware.
                    request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(DailyRequestLimit)
    async def ai_limit_reached(request: Request, error: DailyRequestLimit):
        return JSONResponse({"detail": str(error)}, status_code=429)

    def assets(name: str) -> str:
        return files("career_agent").joinpath("web", name).read_text(encoding="utf-8")

    def current_packet(job_id: str) -> dict[str, Any] | None:
        _id(job_id)
        path = config.packet_dir / f"{job_id}.json"
        _reject_symlink_components(path)
        if not path.is_file():
            return None
        packet = load_packet(path)
        if packet["job_id"] != job_id:
            raise ValueError("Saved packet job ID does not match the requested job")
        return packet

    def active_workspace() -> dict[str, Any]:
        with Store(config.db_path) as store:
            return CandidateWorkspace(store).latest()

    def configured_profile() -> CandidateProfile | None:
        workspace = active_workspace()
        if workspace["revision"] > 0:
            return CandidateWorkspace.scoring_profile(workspace)
        return CandidateProfile.load(config.profile_path) if config.profile_path else None

    def configured_facts() -> FactBank:
        workspace = active_workspace()
        if workspace["revision"] > 0:
            return CandidateWorkspace.fact_bank(workspace)
        if config.facts_path is None:
            raise HTTPException(
                status_code=412,
                detail="Verify career facts in the profile workspace before drafting applications",
            )
        return FactBank.load(config.facts_path)

    def packet_stale(packet: dict[str, Any], revision: int | None = None) -> bool:
        if revision is None:
            revision = active_workspace()["revision"]
        bound_revision = packet.get("candidate_workspace_revision")
        if (bound_revision != revision) if revision > 0 else bound_revision is not None:
            return True
        with Store(config.db_path) as store:
            if packet.get('job_description_revision', 0) != store.description_revision(packet['job_id']):
                return True
            run = store.connection.execute('SELECT input_sha256,packet_sha256 FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1',
                (packet['job_id'],)).fetchone()
        if run:
            if run['packet_sha256'] != packet['packet_sha256']:
                return True
            try:
                return run['input_sha256'] != workflow_signature(current_workflow_inputs(packet['job_id']))
            except (ValueError, HTTPException, OSError):
                return True
        return False

    def job_view(store: Store, job: Job, profile: CandidateProfile | None,
                 records: tuple, workspace_revision: int, posting_checks: PostingChecks,
                 discovery_observations: list[dict], constraints: dict) -> dict:
        packet = current_packet(job.id)
        stale = packet_stale(packet, workspace_revision) if packet else False
        review = store.latest_review(job.id, packet["packet_sha256"]) if packet and not stale else None
        fit = score_job(job, profile) if profile is not None else None
        sponsorship = sponsorship_report(job, records)
        return {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "remote": job.remote,
            "url": job.url,
            "source": job.source,
            "discovery_observations": discovery_observations,
            "live_check_supported": posting_target(job.url) is not None,
            "live_check": posting_checks.latest(job.id),
            "description": job.description,
            "description_revision": store.description_revision(job.id),
            "description_correction": store.description_latest(job.id),
            "application_stage": store.stage_of(job.id),
            "fit_score": fit.score if fit is not None else None,
            "fit_reasons": list(fit.reasons) if fit is not None else [],
            "constraint_evidence": constraint_evidence(job, constraints),
            "title_level_evidence": title_level_evidence(job, constraints),
            "sponsorship_status": sponsorship["status"],
            "sponsorship_reason": sponsorship["reason"],
            "packet_available": packet is not None,
            "packet_stale": stale,
            "review_decision": review["decision"] if review else None,
        }

    @app.get("/", response_class=HTMLResponse)
    def homepage():
        contents = assets("index.html").replace("__SESSION_TOKEN__", html.escape(token, quote=True))
        return HTMLResponse(contents)

    @app.get("/assets/app.js")
    def script():
        return Response(assets("app.js"), media_type="text/javascript")

    @app.get("/assets/app.css")
    def stylesheet():
        return Response(assets("app.css"), media_type="text/css")

    @app.get("/assets/h1b.js")
    def h1b_script():
        return Response(assets("h1b.js"), media_type="text/javascript")

    @app.get("/assets/ai.js")
    def ai_script():
        return Response(assets("ai.js"), media_type="text/javascript")

    @app.get('/assets/automation.js')
    def automation_script():
        return Response(assets('automation.js'), media_type='text/javascript')

    @app.get('/api/automation')
    def automation_status():
        return {**automation.list(), 'workflows': workflows.list()}

    @app.post('/api/automation/searches')
    async def automation_save(request: Request):
        return automation.save(await request.json())

    @app.post('/api/automation/searches/{sid}')
    async def automation_toggle(sid: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm changing recurring discovery')
        if data.get('remove') is True:
            automation.remove(sid)
        else:
            automation.enable(sid, data.get('enabled'))
        return {'saved': True}

    @app.put('/api/automation/searches/{sid}')
    async def automation_edit(sid: str, request: Request):
        return automation.edit(sid, await request.json())

    def current_workflow_inputs(job_id, store=None):
        if store is None:
            with Store(config.db_path) as snapshot:
                snapshot.connection.execute('BEGIN')
                return current_workflow_inputs(job_id, snapshot)
        # The supplied store may already hold BEGIN IMMEDIATE. Avoid constructors
        # that initialize tables or commit, and read all DB inputs in that snapshot.
        revision = store.workspace_revision()
        if revision:
            row = store.connection.execute('SELECT document FROM candidate_workspace_versions WHERE revision=?', (revision,)).fetchone()
            workspace = json.loads(row['document'])
            profile = CandidateWorkspace.scoring_profile(workspace)
            bank = CandidateWorkspace.fact_bank(workspace)
        else:
            profile = CandidateProfile.load(config.profile_path) if config.profile_path else None
            if config.facts_path is None:
                raise ValueError('Verify career facts before drafting')
            bank = FactBank.load(config.facts_path)
        if profile is None:
            raise ValueError('Configure a career profile first')
        job = _get_job(store, job_id)
        job_revision = store.description_revision(job.id)
        run = store.connection.execute('SELECT input_json FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1', (job.id,)).fetchone()
        previous_inputs = json.loads(run['input_json']) if run else {}
        return workflow_inputs(job, profile, bank, load_filing_evidence(config.filings_path), revision or None,
                               job_revision or None, previous_inputs.get('tailored_fact_ids'), previous_inputs.get('tailored_section_order'))

    def workflow_inputs_changed(store, packet):
        run = store.connection.execute('SELECT input_sha256,packet_sha256 FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1',
                                       (packet['job_id'],)).fetchone()
        if not run:
            return False  # Historical standalone CLI packets have no input snapshot.
        if run['packet_sha256'] != packet['packet_sha256']:
            return True
        try:
            return run['input_sha256'] != workflow_signature(current_workflow_inputs(packet['job_id'], store))
        except (ValueError, HTTPException, OSError, KeyError, TypeError):
            return True

    @app.post('/api/workflows/{run_id}/retry')
    async def workflow_retry(run_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm resuming this local workflow')
        row = next((r for r in workflows.list() if r['run_id'] == run_id), None)
        if row is None:
            raise ValueError('Unknown workflow run')
        try:
            state = current_workflow_inputs(row['job_id'])
        except OSError:
            raise ValueError('Workflow inputs are unavailable; restore them before retrying') from None
        result = await run_in_threadpool(workflows.advance, run_id, workflow_signature(state),
                                         expected_workspace_revision=state['workspace_revision'] or 0)
        return {'run_id': run_id, 'status': result['status']}

    @app.get("/api/ai/routes")
    def ai_routes():
        return {"routes": ai.catalog()}

    @app.get("/api/ai/history")
    def ai_history():
        with Store(config.db_path) as store:
            history = AIHistory(store)
            return {"recent": history.recent(), "today": history.today()}

    def route_consent_fingerprint(data):
        fingerprint = data.get('route_fingerprint')
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
            raise ValueError('Reload model routes and confirm the destination before sharing')
        return fingerprint

    @app.post("/api/ai/test-route")
    async def ai_test_route(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {"route", "route_fingerprint", "acknowledged"} or data["acknowledged"] is not True:
            raise ValueError("Confirm that the test contacts the selected provider and may incur charges")
        fingerprint = route_consent_fingerprint(data)
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An AI request is already running")
        try:
            return await run_in_threadpool(run_recorded, config.db_path, data["route"], 'route_test', ai.test_route, data["route"], True, expected_route_fingerprint=fingerprint)
        finally:
            ai_lock.release()

    @app.post("/api/workspace/chat")
    async def workspace_chat(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise ValueError("Confirm sending your question to the selected provider")
        fingerprint = route_consent_fingerprint(data)
        include_facts = data.get("include_verified_facts", False)
        if not isinstance(include_facts, bool):
            raise ValueError("Choose whether to share verified facts")
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An AI request is already running")
        try:
            claims = {}
            if include_facts:
                workspace = active_workspace()
                if workspace["revision"]:
                    claims = {fact["id"]: fact["statement"] for fact in workspace["facts"]
                              if fact["verified_by_candidate"]}
                elif config.facts_path:
                    claims = FactBank.load(config.facts_path).public_claims()
            return await run_in_threadpool(run_recorded, config.db_path, data.get("route"), 'career_chat', ai.profile_chat, data.get("route"), data.get("message"), claims, True, data.get("history", []), expected_route_fingerprint=fingerprint)
        finally:
            ai_lock.release()

    @app.post("/api/workspace/resume-extract")
    async def workspace_resume_extract(request: Request):
        from .resume_import import extract_resume
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {"filename", "content"}:
            raise ValueError("Choose a résumé file to extract locally")
        text = await run_in_threadpool(extract_resume, data["filename"], data["content"])
        return {"text": text}

    @app.post("/api/workspace/ai-resume-suggestions")
    async def ai_resume_suggestions(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'route', 'route_fingerprint', 'resume_text', 'acknowledged'} or data['acknowledged'] is not True:
            raise ValueError('Confirm sending the résumé text to the selected provider')
        fingerprint = route_consent_fingerprint(data)
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An AI request is already running')
        try:
            return await run_in_threadpool(run_recorded, config.db_path, data['route'], 'resume_fact_suggestions',
                                           ai.resume_fact_suggestions, data['route'], data['resume_text'], True,
                                           expected_route_fingerprint=fingerprint)
        finally:
            ai_lock.release()

    @app.post("/api/jobs/{job_id}/ai-analysis")
    async def ai_analysis(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm sending this posting and verified fact statements to the selected provider')
        fingerprint = route_consent_fingerprint(data)
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An AI analysis is already running')
        try:
            with Store(config.db_path) as store:
                job = _get_job(store, job_id)
            bank = configured_facts()
            return await run_in_threadpool(run_recorded, config.db_path, data.get('route'), 'requirements', ai.analyze, job, bank, data.get('route'), True, expected_route_fingerprint=fingerprint)
        finally:
            ai_lock.release()

    @app.post("/api/jobs/{job_id}/ai-tailoring-suggestions")
    async def ai_tailoring_suggestions(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm sending this posting and verified fact statements to the selected provider')
        fingerprint = route_consent_fingerprint(data)
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An AI request is already running')
        try:
            with Store(config.db_path) as store:
                job = _get_job(store, job_id)
            bank = configured_facts()
            return await run_in_threadpool(run_recorded, config.db_path, data.get('route'), 'resume_suggestions', ai.tailoring_suggestions, job, bank, data.get('route'), True, expected_route_fingerprint=fingerprint)
        finally:
            ai_lock.release()

    @app.get("/api/h1b")
    def h1b_settings():
        with Store(config.db_path) as store:
            research = H1BResearch(store)
            return {"settings": research.settings(), "coverage": research.coverage()}

    @app.put("/api/h1b")
    async def h1b_configure(request: Request):
        data = await request.json()
        with Store(config.db_path) as store:
            return H1BResearch(store).configure(data)

    @app.post("/api/h1b/import")
    async def h1b_import(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True or not isinstance(data.get("csv"), str):
            raise ValueError("Choose a public DOL CSV and confirm its provenance")
        if not h1b_import_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An H-1B import is already running; wait before retrying")
        def import_records():
            try:
                with Store(config.db_path) as store:
                    return H1BResearch(store).import_csv(io.StringIO(data["csv"].lstrip('\ufeff')),
                                                       data.get("source_url"), data.get("captured_on"))
            except csv.Error as error:
                raise ValueError("Invalid CSV structure or field size") from error
        try:
            return await run_in_threadpool(import_records)
        finally:
            h1b_import_lock.release()

    @app.post('/api/h1b/import-workbook')
    async def h1b_import_workbook(request: Request):
        if request.headers.get('x-lca-acknowledged') != 'true':
            raise ValueError('Confirm the public DOL source and capture date')
        source_url, captured = validate_lca_source(request.query_params.get('source_url'), request.query_params.get('captured_on'))
        captured_on = captured.isoformat()
        length = request.headers.get('content-length')
        if length is not None and (len(length) > 12 or not length.isascii() or not length.isdecimal() or int(length) > LCA_WORKBOOK_LIMIT):
            raise HTTPException(status_code=413, detail='Workbook exceeds the 512 MB upload limit')
        if not h1b_import_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An H-1B import is already running; wait before retrying')
        try:
            set_h1b_progress('uploading', 0)
            with tempfile.TemporaryDirectory(prefix='career-lca-') as directory:
                path = Path(directory) / 'disclosure.xlsx'
                size = 0
                with path.open('wb') as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > LCA_WORKBOOK_LIMIT:
                            raise HTTPException(status_code=413, detail='Workbook exceeds the 512 MB upload limit')
                        await run_in_threadpool(output.write, chunk)
                        set_h1b_progress('uploading', size)
                if length is not None and size != int(length):
                    raise HTTPException(status_code=400, detail='Incomplete workbook upload; retry')
                if not size:
                    raise ValueError('Choose a nonempty XLSX workbook')
                set_h1b_progress('opening', 0)
                def import_workbook():
                    with Store(config.db_path) as store:
                        return H1BResearch(store).import_xlsx(path, source_url, captured_on, progress=set_h1b_progress)
                return await run_in_threadpool(import_workbook)
        except OSError:
            raise HTTPException(status_code=507, detail='Unable to stage or read the workbook locally; check disk space and retry') from None
        finally:
            set_h1b_progress('idle', 0)
            h1b_import_lock.release()

    @app.post('/api/h1b/check-dol-releases')
    async def h1b_check_dol_releases(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'acknowledged'} or data['acknowledged'] is not True:
            raise ValueError('Confirm the public DOL release check')
        from .dol_releases import check_dol_releases
        return await run_in_threadpool(check_dol_releases)

    @app.post('/api/h1b/refresh-dol')
    async def h1b_refresh_dol(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'source_url', 'acknowledged'} or data['acknowledged'] is not True:
            raise ValueError('Confirm the selected public DOL download')
        if not h1b_import_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An H-1B import is already running; wait before retrying')
        try:
            set_h1b_progress('downloading', 0)
            def refresh():
                with Store(config.db_path) as store:
                    return H1BResearch(store).refresh_dol(data['source_url'], progress=set_h1b_progress)
            return await run_in_threadpool(refresh)
        finally:
            set_h1b_progress('idle', 0)
            h1b_import_lock.release()

    @app.get('/api/h1b/import-progress')
    def h1b_import_progress():
        with h1b_progress_lock:
            return dict(h1b_progress)

    @app.post("/api/h1b/uscis/{action}")
    async def uscis_update(action: str, request: Request):
        if action not in {"import", "refresh"}:
            raise HTTPException(status_code=404, detail="Unknown USCIS action")
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise ValueError("Confirm the USCIS source and reporting date before proceeding")
        if action == "import" and not isinstance(data.get("csv"), str):
            raise ValueError("Choose an official USCIS CSV file")
        if not h1b_import_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An evidence update is already running")
        def update_records():
            with Store(config.db_path) as store:
                records = USCISRecords(store)
                args = (data.get("source_url"), data.get("fiscal_year"), data.get("data_as_of"))
                if action == "refresh":
                    return records.refresh(*args)
                return records.import_bytes(data["csv"].encode('utf-8'), *args)
        try:
            return await run_in_threadpool(update_records)
        finally:
            h1b_import_lock.release()

    @app.post("/api/jobs/{job_id}/live-check")
    async def live_check(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise ValueError("Confirm checking this public posting with its ATS provider")
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
        if not live_check_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A live job check is already running")
        try:
            result = await run_in_threadpool(check_posting, job)
            with Store(config.db_path) as store:
                return PostingChecks(store).save(job_id, result)
        finally:
            live_check_lock.release()

    @app.get("/api/h1b/opportunities")
    def h1b_opportunities(q: str = '', category: str = 'all', offset: int = 0):
        with Store(config.db_path) as store:
            return H1BResearch(store).opportunities(q, category, offset)

    @app.get("/api/h1b/search")
    def h1b_search(q: str, offset: int = 0):
        with Store(config.db_path) as store:
            return H1BResearch(store).search(q, offset)

    @app.get("/api/jobs/{job_id}/h1b")
    def h1b_job(job_id: str):
        with Store(config.db_path) as store:
            return H1BResearch(store).job(_get_job(store, job_id))

    @app.put("/api/jobs/{job_id}/h1b/employer")
    async def h1b_link_employer(job_id: str, request: Request):
        data = await request.json()
        with Store(config.db_path) as store:
            return H1BResearch(store).link_employer(_get_job(store, job_id), data)

    @app.get("/api/jobs/{job_id}/h1b/company-association")
    def h1b_company_association(job_id: str):
        with Store(config.db_path) as store:
            return H1BResearch(store).company_link_history(_get_job(store, job_id))

    @app.get("/api/jobs/{job_id}/h1b/policy-notes")
    def h1b_policy_notes(job_id: str):
        with Store(config.db_path) as store:
            return {"notes": H1BResearch(store).policy_notes(_get_job(store, job_id))}

    @app.post("/api/jobs/{job_id}/h1b/policy-notes")
    async def h1b_add_policy_note(job_id: str, request: Request):
        data = await request.json()
        with Store(config.db_path) as store:
            return {"notes": H1BResearch(store).add_policy_note(_get_job(store, job_id), data)}

    @app.put("/api/jobs/{job_id}/h1b/company-association")
    async def h1b_link_company(job_id: str, request: Request):
        data = await request.json()
        with Store(config.db_path) as store:
            return H1BResearch(store).link_company(_get_job(store, job_id), data)

    @app.get("/api/settings")
    def settings():
        workspace = active_workspace()
        is_saved = workspace["revision"] > 0
        profile = workspace["profile"]
        if not is_saved and config.profile_path:
            try:
                legacy_profile = CandidateProfile.load(config.profile_path)
                targets, skills = legacy_profile.target_titles, legacy_profile.skills
            except (OSError, ValueError):
                targets, skills = (), ()
        else:
            targets, skills = profile["target_titles"], profile["skills"]
        return {
            "profile_configured": bool(workspace["profile"]["name"]) if is_saved else config.profile_path is not None,
            "target_titles_configured": bool(targets),
            "skills_configured": bool(skills),
            "facts_configured": (
                any(fact["verified_by_candidate"] for fact in workspace["facts"])
                if is_saved else config.facts_path is not None
            ),
            "workspace_initialized": is_saved,
            "legacy_import_available": bool(config.profile_path and config.facts_path and not is_saved),
            "filings_configured": config.filings_path is not None,
            "runtime": "local_single_user",
            "application_submission_enabled": False,
        }

    @app.get("/api/workspace")
    def get_workspace():
        return {"workspace": active_workspace()}

    @app.get("/api/workspace/history")
    def workspace_history():
        with Store(config.db_path) as store:
            return {"revisions": CandidateWorkspace(store).history()}

    @app.put("/api/workspace")
    async def save_workspace(request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Workspace must be a JSON object")
        with Store(config.db_path) as store:
            try:
                saved = CandidateWorkspace(store).save(
                    data.get("expected_revision"), data.get("profile"), data.get("facts")
                )
            except StaleRevision as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return {"workspace": saved}

    @app.post("/api/workspace/facts/{fact_id}/verification")
    async def verify_workspace_fact(fact_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Verification decision must be an object")
        with Store(config.db_path) as store:
            try:
                updated = CandidateWorkspace(store).verify(
                    data.get("expected_revision"),
                    fact_id,
                    data.get("verified"),
                    data.get("acknowledged"),
                )
            except StaleRevision as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return {"workspace": updated}

    @app.post("/api/jobs/{job_id}/stage-ai-wording")
    async def stage_ai_wording(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'expected_revision', 'fact_id', 'source_statement', 'job_quote', 'wording', 'acknowledged'}:
            raise ValueError('A reviewed suggestion and its source are required')
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            try:
                updated = CandidateWorkspace(store).stage_wording(data['expected_revision'], job_id,
                    data['fact_id'], data['source_statement'], data['job_quote'], data['wording'], data['acknowledged'])
            except StaleRevision as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return {'workspace': updated}

    @app.post("/api/workspace/import")
    async def import_legacy_workspace(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise HTTPException(status_code=422, detail="Explicit legacy import acknowledgement is required")
        if config.profile_path is None or config.facts_path is None:
            raise HTTPException(status_code=412, detail="Configure --profile and --facts before importing")
        profile = CandidateProfile.load(config.profile_path)
        facts = FactBank.load(config.facts_path)
        with Store(config.db_path) as store:
            try:
                imported = CandidateWorkspace(store).import_legacy(profile, facts)
            except StaleRevision as error:
                raise HTTPException(status_code=409, detail="Workspace already exists; import would overwrite it") from error
        return {"workspace": imported}

    @app.get("/api/jobs")
    def list_jobs(before: int | None = None, limit: int = 500):
        workspace = active_workspace()
        profile = (
            CandidateWorkspace.scoring_profile(workspace)
            if workspace["revision"] > 0
            else CandidateProfile.load(config.profile_path) if config.profile_path else None
        )
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            posting_checks = PostingChecks(store)
            store.connection.execute('BEGIN')
            jobs, next_before = store.job_page(limit=limit, before=before)
            observations = store.discovery_observations([job.id for job in jobs])
            items = [
                job_view(store, job, profile, records, workspace["revision"], posting_checks,
                         observations.get(job.id, []), workspace["profile"] if workspace["revision"] else {})
                for job in jobs
            ]
        return {"jobs": items, "total": len(items), "next_before": next_before}

    @app.post("/api/discover")
    async def discover(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise HTTPException(status_code=422, detail="Confirm sending this search to external job sources")
        query, location, results = data.get("query"), data.get("location"), data.get("results", 20)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise HTTPException(status_code=422, detail="Search query must be 1–200 characters")
        if not isinstance(location, str) or not 1 <= len(location.strip()) <= 240:
            raise HTTPException(status_code=422, detail="Location must be 1–240 characters")
        if type(results) is not int or not 1 <= results <= 100:
            raise HTTPException(status_code=422, detail="Results per source must be an integer from 1 to 100")
        sites = data.get('sites')
        if 'sites' in data:
            try:
                sites = validate_sites(sites)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None
        if not discovery_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A job search is already running. Wait for it to finish.")
        try:
            try:
                if sites is None:
                    jobs = await run_in_threadpool(discover_isolated, query.strip(), location.strip(), results, automation.stop)
                else:
                    jobs = await run_in_threadpool(discover_isolated, query.strip(), location.strip(), results, automation.stop, sites=sites)
            except DiscoveryUnavailable as error:
                raise HTTPException(status_code=503, detail='Job discovery unavailable. Install the jobspy extra and retry.') from error
            except DiscoveryTimedOut as error:
                raise HTTPException(status_code=504, detail='Job discovery timed out; no results were imported. Try again or narrow the search.') from error
            except DiscoveryStopped as error:
                raise HTTPException(status_code=503, detail='Job discovery stopped; no results were imported.') from error
            except Exception as error:
                raise HTTPException(status_code=502, detail="Job discovery failed. Sources may be unavailable or rate-limited; try again later.") from error
            with Store(config.db_path) as store:
                added = store.add_discovered_jobs(jobs)
            result = {"found": len(jobs), "new": added, "duplicates": len(jobs) - added}
            if getattr(jobs, 'source_status', None):
                result['source_status'] = jobs.source_status
            return result
        finally:
            discovery_lock.release()

    @app.get("/api/overview")
    def overview():
        with Store(config.db_path) as store:
            jobs = store.list_jobs(limit=10000)
            reviewed = store.review_history(limit=100)
            submitted = sum(store.stage_of(job.id) == "submitted" for job in jobs)
            pending = sum(store.stage_of(job.id) == "ready_for_review" for job in jobs)
        return {
            "total_jobs": len(jobs),
            "submitted_recorded": submitted,
            "staged": pending,
            "review_events_recent": len(reviewed),
            "submission_sent_by_application": False,
        }

    @app.post("/api/jobs", status_code=201)
    async def add_job(request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Job must be a JSON object")
        job = Job.from_mapping(data, source="manual")
        if len(job.title) > 200 or len(job.company) > 200 or len(job.location) > 240 or len(job.description) > 50000:
            raise HTTPException(status_code=422, detail="Job fields exceed local input limits")
        with Store(config.db_path) as store:
            inserted = store.add_jobs([job])
        return {"job_id": job.id, "created": inserted == 1}

    @app.get("/api/jobs/{job_id}/possible-duplicates")
    def possible_duplicates(job_id: str):
        with Store(config.db_path) as store:
            store.connection.execute('BEGIN')
            _get_job(store, job_id)
            return {'job_id': job_id, **store.possible_duplicates(job_id)}

    @app.get("/api/jobs/{job_id}/description-history")
    def description_history(job_id: str):
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
            return {"job_id": job.id, "revisions": store.description_history(job.id)}

    @app.get("/api/jobs/{job_id}/fit-evidence")
    def fit_evidence(job_id: str):
        from .fit_evidence import requirement_evidence
        with Store(config.db_path) as store:
            candidate = CandidateWorkspace(store)
            store.connection.execute('BEGIN')
            workspace = candidate.latest()
            job = _get_job(store, job_id)
            job_revision = store.description_revision(job.id)
        if workspace['revision']:
            facts = [fact for fact in workspace['facts'] if fact['verified_by_candidate']]
        elif config.facts_path:
            bank = FactBank.load(config.facts_path)
            facts = [{'id': fact.fact_id, 'statement': fact.statement, 'keywords': fact.keywords}
                     for fact in bank.facts]
        else:
            facts = []
        return {'job_id': job.id, 'description_revision': job_revision,
                'workspace_revision': workspace['revision'], **requirement_evidence(job.description, facts)}

    @app.get('/api/jobs/{job_id}/interview-preparation')
    def interview_preparation(job_id: str):
        from .interview_prep import preparation_guide
        return preparation_guide(fit_evidence(job_id))

    @app.put("/api/jobs/{job_id}/description")
    async def correct_job_description(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise HTTPException(status_code=422, detail='Confirm that you reviewed the corrected posting')
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
            try:
                return store.correct_description(job.id, data.get('description'), data.get('expected_revision'), data.get('source_note', ''))
            except DescriptionConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from None

    @app.get("/api/tailoring/facts")
    def tailoring_facts():
        workspace = active_workspace()
        bank = CandidateWorkspace.fact_bank(workspace) if workspace["revision"] > 0 else configured_facts()
        facts = [{'id': fact.fact_id, 'section': fact.section, 'statement': fact.statement} for fact in bank.facts]
        return {'workspace_revision': workspace['revision'], 'facts': facts, 'facts_sha256': facts_hash(facts),
                'max_facts': MAX_TAILORED_FACTS}

    def facts_hash(facts):
        return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def create_draft(job_id: str, selection=None, expected_revision=None, expected_facts=None, section_order=None):
        workspace = active_workspace()
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != workspace['revision']):
            raise HTTPException(status_code=409, detail='Career profile changed; reload verified facts before tailoring')
        if workspace["revision"] > 0:
            profile = CandidateWorkspace.scoring_profile(workspace)
            bank = CandidateWorkspace.fact_bank(workspace)
        else:
            profile = configured_profile()
            if profile is None:
                raise HTTPException(status_code=412, detail="Configure a career profile before drafting applications")
            bank = configured_facts()
        if expected_facts is not None:
            visible = [{'id': fact.fact_id, 'section': fact.section, 'statement': fact.statement} for fact in bank.facts]
            if not isinstance(expected_facts, str) or not hmac.compare_digest(expected_facts, facts_hash(visible)):
                raise HTTPException(status_code=409, detail='Verified facts changed; reload them before tailoring')
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            store.connection.execute('BEGIN')
            job = _get_job(store, job_id)
            job_revision = store.description_revision(job.id)
            if store.stage_of(job.id) == "submitted":
                raise HTTPException(
                    status_code=409,
                    detail="This application is already recorded as submitted; its reviewed packet is locked",
                )
        if selection is not None:
            verified = bank.public_claims()
            if (not isinstance(selection, list) or not 1 <= len(selection) <= MAX_TAILORED_FACTS
                    or not all(isinstance(item, str) and item in verified for item in selection)
                    or len(set(selection)) != len(selection)):
                raise ValueError(f'Select 1–{MAX_TAILORED_FACTS} distinct verified career facts')
        if section_order is not None and (not isinstance(section_order, list) or len(section_order) != len(SECTIONS)
                or not all(isinstance(item, str) and item in SECTIONS for item in section_order)
                or len(set(section_order)) != len(SECTIONS) or selection is None):
            raise ValueError('Choose each resume section exactly once')
        run = workflows.start(workflow_inputs(job, profile, bank, records, workspace['revision'] or None, job_revision or None, selection, section_order), guard_workspace=True)
        packet = run['packet']
        return {
            "job_id": job.id,
            "packet_sha256": packet["packet_sha256"],
            "status": "awaiting_human_review",
            "run_id": run['run_id'],
            "submission_sent": False,
        }

    @app.post("/api/jobs/{job_id}/draft")
    def draft(job_id: str):
        return create_draft(job_id)

    @app.post("/api/jobs/{job_id}/tailored-draft")
    async def tailored_draft(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) not in ({'fact_ids', 'workspace_revision', 'facts_sha256'}, {'fact_ids', 'workspace_revision', 'facts_sha256', 'section_order'}):
            raise ValueError('Select verified facts and include the profile revision')
        if type(data['workspace_revision']) is not int or data['workspace_revision'] < 0:
            raise ValueError('Invalid profile revision')
        if not isinstance(data['fact_ids'], list):
            raise ValueError(f'Select 1–{MAX_TAILORED_FACTS} distinct verified career facts')
        if 'section_order' in data and not isinstance(data['section_order'], list):
            raise ValueError('Choose each resume section exactly once')
        if not isinstance(data['facts_sha256'], str) or len(data['facts_sha256']) != 64 or any(c not in '0123456789abcdef' for c in data['facts_sha256']):
            raise ValueError('Invalid verified facts fingerprint')
        return await run_in_threadpool(create_draft, job_id, data['fact_ids'], data['workspace_revision'], data['facts_sha256'], data.get('section_order'))

    @app.get("/api/jobs/{job_id}/packet")
    def get_packet(job_id: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            packet = current_packet(job_id)
            if packet is None:
                raise HTTPException(status_code=404, detail="No packet has been drafted for this job")
            stale = packet_stale(packet)
            reviewed = store.latest_review(job_id, packet["packet_sha256"]) if not stale else None
        return {
            "packet": packet,
            "review": reviewed,
            "stale": stale,
            "submission_sent_by_application": False,
        }

    @app.get("/api/jobs/{job_id}/packet/versions")
    def packet_versions(job_id: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
        return {"versions": workflows.versions(job_id)}

    @app.get("/api/jobs/{job_id}/packet/versions/{run_id}")
    def packet_version(job_id: str, run_id: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
        return workflows.historical_packet(job_id, run_id)

    @app.post("/api/jobs/{job_id}/decision")
    async def record_decision(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or type(data.get("approved")) is not bool or data.get("acknowledged") is not True:
            raise HTTPException(status_code=422, detail="Explicit approval/rejection and acknowledgement required")
        current = current_packet(job_id)
        if current is None:
            raise HTTPException(status_code=404, detail="No packet available to review")
        with Store(config.db_path) as store:
            if store.stage_of(job_id) == 'submitted':
                raise ValueError('A submitted application cannot receive a new review decision')
        if packet_stale(current):
            raise HTTPException(status_code=409, detail="Career profile or job description changed; regenerate this draft before review")
        fingerprint = data.get("packet_sha256")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, current["packet_sha256"]):
            raise HTTPException(status_code=409, detail="Stale packet; reload and review the latest draft")
        handled = await run_in_threadpool(workflows.review, job_id, fingerprint, data['approved'],
                                          lambda: current_workflow_inputs(job_id),
                                          lambda store: current_workflow_inputs(job_id, store))
        with Store(config.db_path) as store:
            try:
                result = store.latest_review(job_id, fingerprint) if handled else store.record_review(
                    job_id, fingerprint, approved=data["approved"],
                    expected_description_revision=current.get('job_description_revision', 0),
                    expected_workspace_revision=current.get('candidate_workspace_revision') or 0)
            except DescriptionConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
        return {**result, "submission_sent": False}

    def approved_document_packet(job_id: str, packet_sha256: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            packet = current_packet(job_id)
            if packet is None:
                raise HTTPException(status_code=404, detail="Draft and review a packet before exporting")
            if not re.fullmatch(r"[a-f0-9]{64}", packet_sha256) or not hmac.compare_digest(packet_sha256, packet["packet_sha256"]) or packet_stale(packet):
                raise HTTPException(status_code=409, detail="Packet changed; reload, regenerate and review before export")
            decision = store.latest_review(job_id, packet_sha256)
            if not decision or decision["decision"] != "approved_for_manual_submission":
                raise HTTPException(status_code=412, detail="Approve this packet before downloading its documents")
        return packet

    def recheck_export(job_id: str, packet_sha256: str, packet: dict):
        with Store(config.db_path) as store, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            latest = current_packet(job_id)
            if (store.description_revision(job_id) != packet.get('job_description_revision', 0)
                    or store.workspace_revision() != (packet.get('candidate_workspace_revision') or 0)
                    or latest is None or latest['packet_sha256'] != packet_sha256
                    or workflow_inputs_changed(store, packet)):
                raise HTTPException(status_code=409, detail='Packet or job description changed during export; reload and review')
            decision = store.latest_review(job_id, packet_sha256)
            if not decision or decision['decision'] != 'approved_for_manual_submission':
                raise HTTPException(status_code=409, detail='Review changed during export; reload and review')

    @app.get("/api/jobs/{job_id}/resume.pdf")
    def export_resume(job_id: str, packet_sha256: str = ""):
        packet = approved_document_packet(job_id, packet_sha256)
        try:
            content = resume_pdf(packet)
        except PDFUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        recheck_export(job_id, packet_sha256, packet)
        return Response(content, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="resume-{job_id}-{packet_sha256[:12]}.pdf"',
        })

    @app.get("/api/jobs/{job_id}/resume.docx")
    def export_resume_docx(job_id: str, packet_sha256: str = ""):
        packet = approved_document_packet(job_id, packet_sha256)
        try:
            content = resume_docx(packet)
        except DOCXUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        recheck_export(job_id, packet_sha256, packet)
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={
            "Content-Disposition": f'attachment; filename="resume-{job_id}-{packet_sha256[:12]}.docx"',
        })

    @app.get("/api/jobs/{job_id}/cover-note.txt")
    def export_cover_note(job_id: str, packet_sha256: str = ""):
        packet = approved_document_packet(job_id, packet_sha256)
        note = packet.get('cover_note')
        if (not isinstance(note, str) or not note.strip() or len(note) > 200_000
                or any((ord(c) < 32 and c not in '\t\r\n') or 0xd800 <= ord(c) <= 0xdfff for c in note)):
            raise ValueError('The reviewed packet has no valid cover note to export')
        content = note.encode('utf-8')
        recheck_export(job_id, packet_sha256, packet)
        return Response(content, media_type='text/plain; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="cover-note-{job_id}-{packet_sha256[:12]}.txt"',
        })

    @app.post("/api/jobs/{job_id}/submitted")
    async def mark_submitted(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("attest_confirmed") is not True:
            raise HTTPException(status_code=422, detail="Explicit attestation of manual employer submission required")
        receipt = data.get("receipt")
        if not isinstance(receipt, str) or not 4 <= len(receipt.strip()) <= 200:
            raise HTTPException(status_code=422, detail="An employer confirmation reference of 4-200 characters is required")
        packet = current_packet(job_id)
        if packet is None:
            raise HTTPException(status_code=412, detail="Draft and review an application packet first")
        if packet_stale(packet):
            raise HTTPException(status_code=409, detail="Career profile or job description changed; regenerate and review before recording submission")
        fingerprint = data.get("packet_sha256")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, packet["packet_sha256"]):
            raise HTTPException(status_code=409, detail="Stale or missing packet fingerprint")
        with Store(config.db_path) as store, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            latest = current_packet(job_id)
            if (store.description_revision(job_id) != packet.get('job_description_revision', 0)
                    or store.workspace_revision() != (packet.get('candidate_workspace_revision') or 0)
                    or latest is None or latest['packet_sha256'] != fingerprint
                    or workflow_inputs_changed(store, packet)):
                raise HTTPException(status_code=409, detail='Job description or packet changed; regenerate and review')
            decision = store.latest_review(job_id, fingerprint)
            if not decision or decision["decision"] != "approved_for_manual_submission":
                raise HTTPException(status_code=412, detail="Human approval of the current packet is required first")
            if store.stage_of(job_id) == "submitted":
                raise HTTPException(status_code=409, detail="Job is already recorded as submitted")
            store.connection.execute('''INSERT INTO applications(job_id,stage,receipt)
                VALUES(?,'submitted',?) ON CONFLICT(job_id) DO UPDATE SET
                stage='submitted',receipt=excluded.receipt,updated_at=CURRENT_TIMESTAMP''',
                (job_id, receipt.strip()))
        return {
            "job_id": job_id,
            "application_stage": "submitted",
            "recorded_by": "candidate_attestation_of_external_submission",
            "submission_sent_by_application": False,
        }

    @app.get("/api/reviews")
    def reviews():
        with Store(config.db_path) as store:
            return {"reviews": store.review_history(limit=50)}

    @app.get("/api/crm/export")
    def export_crm_history():
        with Store(config.db_path) as store:
            snapshot = ApplicationCRM(store).export_history()
        return Response(json.dumps(snapshot, ensure_ascii=False, indent=2),
                        media_type="application/json", headers={
                            "Content-Disposition": 'attachment; filename="career-tracker-history.json"',
                        })

    @app.get("/api/crm")
    def crm_summary():
        with Store(config.db_path) as store:
            return ApplicationCRM(store).summary()

    @app.get("/api/jobs/{job_id}/crm")
    def crm_details(job_id: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).details(job_id)

    @app.post("/api/jobs/{job_id}/crm/stage")
    async def crm_change_stage(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Pipeline update must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).change_stage(
                job_id, data.get("stage"), data.get("note", ""),
            )

    @app.post("/api/jobs/{job_id}/crm/correct-terminal")
    async def crm_correct_terminal(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'expected_event_id', 'reason', 'acknowledged'}:
            raise ValueError('A correction reason and explicit acknowledgement are required')
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            try:
                return ApplicationCRM(store).correct_terminal(job_id, data['expected_event_id'], data['reason'], data['acknowledged'])
            except StageConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from None

    @app.post("/api/jobs/{job_id}/crm/priority")
    async def crm_priority(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Priority update must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).set_priority(job_id, data.get("priority"))

    @app.post("/api/jobs/{job_id}/crm/interview-notes", status_code=201)
    async def crm_interview_note(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Interview note must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).add_interview_note(
                job_id, data.get("interview_date"), data.get("round"), data.get("notes"),
            )

    @app.post("/api/jobs/{job_id}/crm/contacts", status_code=201)
    async def crm_add_contact(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Contact must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).add_contact(
                job_id, data.get("name"), data.get("role", ""),
                data.get("email", ""), data.get("notes", ""),
            )

    @app.post("/api/jobs/{job_id}/crm/contacts/{contact_id}")
    async def crm_update_contact(job_id: str, contact_id: int, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Contact must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).update_contact(
                job_id, contact_id, data.get("name"), data.get("role", ""),
                data.get("email", ""), data.get("notes", ""),
            )

    @app.post("/api/jobs/{job_id}/crm/followups", status_code=201)
    async def crm_add_followup(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="Follow-up must be an object")
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            return ApplicationCRM(store).add_followup(
                job_id, data.get("title"), data.get("due_date"),
                data.get("contact_id"),
            )

    @app.post("/api/crm/followups/{followup_id}/complete")
    async def crm_complete_followup(followup_id: int, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise HTTPException(status_code=422, detail="Explicit completion confirmation is required")
        with Store(config.db_path) as store:
            return ApplicationCRM(store).update_followup(followup_id, complete=True)

    @app.post("/api/crm/followups/{followup_id}/reschedule")
    async def crm_reschedule_followup(followup_id: int, request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="New due date must be an object")
        with Store(config.db_path) as store:
            return ApplicationCRM(store).update_followup(
                followup_id, due_date=data.get("due_date"),
            )

    @app.post("/api/crm/followups/{followup_id}/cancel")
    async def crm_cancel_followup(followup_id: int, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise HTTPException(status_code=422, detail="Explicit cancellation confirmation is required")
        with Store(config.db_path) as store:
            return ApplicationCRM(store).update_followup(followup_id, cancel=True)

    return app


def serve(config: DashboardConfig, port: int = 8765) -> None:
    """Bind to loopback only; approved discovery schedules run during server lifetime."""
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError('Install local dashboard: pip install -e ".[web]"') from error
    app = create_app(config)
    print(f"Local dashboard: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
