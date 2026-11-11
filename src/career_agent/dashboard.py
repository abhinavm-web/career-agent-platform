"""Local-only dashboard with explicitly requested JobSpy discovery; no submissions.

Threat model: a single trusted user at 127.0.0.1. This is NOT a hosted SaaS.
A random per-process session token protects API reads and writes; host and Origin
are checked to reduce cross-origin and DNS rebinding risks.
"""
from __future__ import annotations

import hmac
import html
import json
import io
import csv
import re
import secrets
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

from .application import _reject_symlink_components, load_packet
from .candidate import FactBank
from . import ai
from .automation import Automation
from .workflows import Workflows, inputs as workflow_inputs, signature as workflow_signature
from .crm import ApplicationCRM
from .discovery import DiscoveryUnavailable, discover_jobspy
from .models import CandidateProfile, Job
from .h1b import H1BResearch
from .uscis import USCISRecords
from .posting_checks import PostingChecks, check_posting, target as posting_target
from .resume_pdf import PDFUnavailable, resume_pdf
from .scoring import score_job
from .sponsor_evidence import load_filing_evidence, sponsorship_report
from .storage import Store
from .workspace import CandidateWorkspace, StaleRevision

JOB_ID = re.compile(r"[a-f0-9]{16}\Z")
API_BODY_LIMIT = 128 * 1024
# Full workspace saves can contain 100 validated statements and private notes.
WORKSPACE_BODY_LIMIT = 2 * 1024 * 1024


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
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return JSONResponse({"detail": "Use application/json"}, status_code=415)
                body = await request.body()
                limit = (
                    10 * 1024 * 1024 if request.url.path in {"/api/h1b/import", "/api/h1b/uscis/import"} else
                    3 * 1024 * 1024 if request.url.path == "/api/workspace/resume-extract" else
                    WORKSPACE_BODY_LIMIT
                    if request.url.path == "/api/workspace" and request.method == "PUT"
                    else API_BODY_LIMIT
                )
                if len(body) > limit:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
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
            run = store.connection.execute('SELECT input_sha256,packet_sha256 FROM workflow_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1',
                (packet['job_id'],)).fetchone()
        if run:
            if run['packet_sha256'] != packet['packet_sha256']:
                return True
            try:
                return run['input_sha256'] != workflow_signature(current_workflow_inputs(packet['job_id']))
            except (ValueError, HTTPException):
                return True
        return False

    def job_view(store: Store, job: Job, profile: CandidateProfile | None,
                 records: tuple, workspace_revision: int, posting_checks: PostingChecks) -> dict:
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
            "live_check_supported": posting_target(job.url) is not None,
            "live_check": posting_checks.latest(job.id),
            "description": job.description,
            "application_stage": store.stage_of(job.id),
            "fit_score": fit.score if fit is not None else None,
            "fit_reasons": list(fit.reasons) if fit is not None else [],
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

    def current_workflow_inputs(job_id):
        workspace = active_workspace()
        profile = configured_profile()
        if profile is None:
            raise ValueError('Configure a career profile first')
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
        return workflow_inputs(job, profile, configured_facts(), load_filing_evidence(config.filings_path), workspace['revision'] or None)

    @app.post('/api/workflows/{run_id}/retry')
    async def workflow_retry(run_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm resuming this local workflow')
        row = next((r for r in workflows.list() if r['run_id'] == run_id), None)
        if row is None:
            raise ValueError('Unknown workflow run')
        state = current_workflow_inputs(row['job_id'])
        result = await run_in_threadpool(workflows.advance, run_id, workflow_signature(state))
        return {'run_id': run_id, 'status': result['status']}

    @app.get("/api/ai/routes")
    def ai_routes():
        return {"routes": ai.catalog()}

    @app.post("/api/workspace/chat")
    async def workspace_chat(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get("acknowledged") is not True:
            raise ValueError("Confirm sending your question to the selected provider")
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
            return await run_in_threadpool(ai.profile_chat, data.get("route"), data.get("message"), claims, True, data.get("history", []))
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

    @app.post("/api/jobs/{job_id}/ai-analysis")
    async def ai_analysis(job_id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or data.get('acknowledged') is not True:
            raise ValueError('Confirm sending this posting and verified fact statements to the selected provider')
        if not ai_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='An AI analysis is already running')
        try:
            with Store(config.db_path) as store:
                job = _get_job(store, job_id)
            bank = configured_facts()
            return await run_in_threadpool(ai.analyze, job, bank, data.get('route'), True)
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

    @app.get("/api/settings")
    def settings():
        workspace = active_workspace()
        is_saved = workspace["revision"] > 0
        return {
            "profile_configured": bool(workspace["profile"]["name"]) if is_saved else config.profile_path is not None,
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
    def list_jobs():
        workspace = active_workspace()
        profile = (
            CandidateWorkspace.scoring_profile(workspace)
            if workspace["revision"] > 0
            else CandidateProfile.load(config.profile_path) if config.profile_path else None
        )
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            posting_checks = PostingChecks(store)
            items = [
                job_view(store, job, profile, records, workspace["revision"], posting_checks)
                for job in store.list_jobs(limit=500)
            ]
        return {"jobs": items, "total": len(items)}

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
        if not discovery_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A job search is already running. Wait for it to finish.")
        try:
            try:
                jobs = await run_in_threadpool(discover_jobspy, query.strip(), location.strip(), results)
            except DiscoveryUnavailable as error:
                raise HTTPException(status_code=503, detail='Job discovery unavailable. Install the jobspy extra and retry.') from error
            except Exception as error:
                raise HTTPException(status_code=502, detail="Job discovery failed. Sources may be unavailable or rate-limited; try again later.") from error
            with Store(config.db_path) as store:
                added = store.add_jobs(jobs)
            return {"found": len(jobs), "new": added, "duplicates": len(jobs) - added}
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

    @app.post("/api/jobs/{job_id}/draft")
    def draft(job_id: str):
        workspace = active_workspace()
        if workspace["revision"] > 0:
            profile = CandidateWorkspace.scoring_profile(workspace)
            bank = CandidateWorkspace.fact_bank(workspace)
        else:
            profile = configured_profile()
            if profile is None:
                raise HTTPException(status_code=412, detail="Configure a career profile before drafting applications")
            bank = configured_facts()
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
            if store.stage_of(job.id) == "submitted":
                raise HTTPException(
                    status_code=409,
                    detail="This application is already recorded as submitted; its reviewed packet is locked",
                )
        run = workflows.start(workflow_inputs(job, profile, bank, records, workspace['revision'] or None))
        packet = run['packet']
        return {
            "job_id": job.id,
            "packet_sha256": packet["packet_sha256"],
            "status": "awaiting_human_review",
            "run_id": run['run_id'],
            "submission_sent": False,
        }

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
            raise HTTPException(status_code=409, detail="Career profile changed; regenerate this draft before review")
        fingerprint = data.get("packet_sha256")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, current["packet_sha256"]):
            raise HTTPException(status_code=409, detail="Stale packet; reload and review the latest draft")
        handled = await run_in_threadpool(workflows.review, job_id, fingerprint, data['approved'], lambda: current_workflow_inputs(job_id))
        with Store(config.db_path) as store:
            result = store.latest_review(job_id, fingerprint) if handled else store.record_review(job_id, fingerprint, approved=data["approved"])
        return {**result, "submission_sent": False}

    @app.get("/api/jobs/{job_id}/resume.pdf")
    def export_resume(job_id: str, packet_sha256: str = ""):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            packet = current_packet(job_id)
            if packet is None:
                raise HTTPException(status_code=404, detail="Draft and review a packet before exporting")
            if not re.fullmatch(r"[a-f0-9]{64}", packet_sha256) or not hmac.compare_digest(packet_sha256, packet["packet_sha256"]) or packet_stale(packet):
                raise HTTPException(status_code=409, detail="Packet changed; reload, regenerate and review before export")
            decision = store.latest_review(job_id, packet_sha256)
            if not decision or decision["decision"] != "approved_for_manual_submission":
                raise HTTPException(status_code=412, detail="Approve this packet before downloading its resume")
        try:
            content = resume_pdf(packet)
        except PDFUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return Response(content, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="resume-{job_id}-{packet_sha256[:12]}.pdf"',
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
            raise HTTPException(status_code=409, detail="Career profile changed; regenerate and review before recording submission")
        fingerprint = data.get("packet_sha256")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, packet["packet_sha256"]):
            raise HTTPException(status_code=409, detail="Stale or missing packet fingerprint")
        with Store(config.db_path) as store:
            decision = store.latest_review(job_id, fingerprint)
            if not decision or decision["decision"] != "approved_for_manual_submission":
                raise HTTPException(status_code=412, detail="Human approval of the current packet is required first")
            if store.stage_of(job_id) == "submitted":
                raise HTTPException(status_code=409, detail="Job is already recorded as submitted")
            store.stage(job_id)
            store.confirm(job_id, receipt, attested=True)
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
