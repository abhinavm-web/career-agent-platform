"""Local-only FastAPI dashboard. No accounts, telemetry, ATS submissions or external API calls.

Threat model: a single trusted user at 127.0.0.1. This is NOT a hosted SaaS.
A random per-process session token protects API reads and writes; host and Origin
are checked to reduce cross-origin and DNS rebinding risks.
"""
from __future__ import annotations

import hmac
import html
import re
import secrets
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .application import _reject_symlink_components, build_packet, load_packet, save_packet
from .candidate import FactBank
from .models import CandidateProfile, Job
from .scoring import score_job
from .sponsor_evidence import load_filing_evidence, sponsorship_report
from .storage import Store

JOB_ID = re.compile(r"[a-f0-9]{16}\Z")
API_BODY_LIMIT = 128 * 1024


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
    app = FastAPI(
        title="Career Agent Local Workspace",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )
    token = secrets.token_urlsafe(32)

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
                if len(body) > API_BODY_LIMIT:
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

    def configured_profile() -> CandidateProfile | None:
        return CandidateProfile.load(config.profile_path) if config.profile_path else None

    def configured_facts() -> FactBank:
        if config.facts_path is None:
            raise HTTPException(status_code=412, detail="Start the dashboard with --facts to draft applications")
        return FactBank.load(config.facts_path)

    def job_view(store: Store, job: Job, profile: CandidateProfile | None, records: tuple) -> dict:
        packet = current_packet(job.id)
        review = store.latest_review(job.id, packet["packet_sha256"]) if packet else None
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
            "description": job.description,
            "application_stage": store.stage_of(job.id),
            "fit_score": fit.score if fit is not None else None,
            "fit_reasons": list(fit.reasons) if fit is not None else [],
            "sponsorship_status": sponsorship["status"],
            "sponsorship_reason": sponsorship["reason"],
            "packet_available": packet is not None,
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

    @app.get("/api/settings")
    def settings():
        return {
            "profile_configured": config.profile_path is not None,
            "facts_configured": config.facts_path is not None,
            "filings_configured": config.filings_path is not None,
            "runtime": "local_single_user",
            "application_submission_enabled": False,
        }

    @app.get("/api/jobs")
    def list_jobs():
        profile = configured_profile()
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            items = [
                job_view(store, job, profile, records)
                for job in store.list_jobs(limit=500)
            ]
        return {"jobs": items, "total": len(items)}

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
        profile = configured_profile()
        if profile is None:
            raise HTTPException(status_code=412, detail="Start the dashboard with --profile to draft applications")
        bank = configured_facts()
        records = load_filing_evidence(config.filings_path)
        with Store(config.db_path) as store:
            job = _get_job(store, job_id)
        packet = build_packet(job, profile, bank, records)
        save_packet(packet, config.packet_dir / f"{job.id}.json")
        return {
            "job_id": job.id,
            "packet_sha256": packet["packet_sha256"],
            "status": "awaiting_human_review",
            "submission_sent": False,
        }

    @app.get("/api/jobs/{job_id}/packet")
    def get_packet(job_id: str):
        with Store(config.db_path) as store:
            _get_job(store, job_id)
            packet = current_packet(job_id)
            if packet is None:
                raise HTTPException(status_code=404, detail="No packet has been drafted for this job")
            reviewed = store.latest_review(job_id, packet["packet_sha256"])
        return {
            "packet": packet,
            "review": reviewed,
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
        fingerprint = data.get("packet_sha256")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, current["packet_sha256"]):
            raise HTTPException(status_code=409, detail="Stale packet; reload and review the latest draft")
        with Store(config.db_path) as store:
            result = store.record_review(job_id, fingerprint, approved=data["approved"])
        return {**result, "submission_sent": False}

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

    return app


def serve(config: DashboardConfig, port: int = 8765) -> None:
    """Bind to loopback only; no host override, background jobs or telemetry."""
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError('Install local dashboard: pip install -e ".[web]"') from error
    app = create_app(config)
    print(f"Local dashboard: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
