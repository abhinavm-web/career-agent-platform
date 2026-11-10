"""CLI: discover, fit-score and manually confirm applications."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from .discovery import discover_jobspy, read_csv_jobs
from .application import build_packet, load_packet, save_packet
from .candidate import FactBank
from .sponsor_evidence import load_filing_evidence, sponsorship_report
from .models import CandidateProfile
from .scoring import score_job
from .sponsorship import assess_sponsorship, load_historical_evidence
from .storage import Store


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Privacy-first career agent MVP")
    root.add_argument("--db", default=".local/career_agent.sqlite3", help="Local SQLite path")
    cmd = root.add_subparsers(dest="command", required=True)

    ingest = cmd.add_parser("ingest", help="Import jobs from CSV")
    ingest.add_argument("csv")

    discover = cmd.add_parser("discover", help="Run optional JobSpy manually")
    discover.add_argument("--query", required=True)
    discover.add_argument("--location", default="United States")
    discover.add_argument("--results", type=int, default=20)

    rank = cmd.add_parser("rank", help="Explain job/profile fit")
    rank.add_argument("--profile", required=True)
    rank.add_argument("--evidence", help="Historical employer CSV with source_url")
    rank.add_argument("--top", type=int, default=20)

    stage = cmd.add_parser("stage", help="Prepare for human review; never submits")
    stage.add_argument("job_id")

    confirm = cmd.add_parser("confirm", help="Record user's verified manual submission")
    confirm.add_argument("job_id")
    confirm.add_argument("--receipt", required=True)
    confirm.add_argument("--attest-confirmed", action="store_true")

    sponsor = cmd.add_parser("sponsorship", help="Research job sponsorship and dated employer records")
    sponsor.add_argument("job_id")
    sponsor.add_argument("--filings", help="CSV with employer, fiscal_year, source_url")

    draft = cmd.add_parser("draft", help="Prepare a local, fact-grounded application packet")
    draft.add_argument("job_id")
    draft.add_argument("--profile", required=True, help="Local preferences JSON")
    draft.add_argument("--facts", required=True, help="Local candidate-verified fact bank")
    draft.add_argument("--filings", help="Source-backed, candidate-provided filing CSV")
    draft.add_argument("--output", help="Optional local packet destination")

    review = cmd.add_parser("review", help="Interactively review a saved packet using LangGraph")
    review.add_argument("packet", help="Local application packet JSON file")

    flow = cmd.add_parser("workflow", help="Run LangGraph analysis, draft, and human review")
    flow.add_argument("job_id")
    flow.add_argument("--profile", required=True)
    flow.add_argument("--facts", required=True)
    flow.add_argument("--filings")
    flow.add_argument("--output", help="Optional local packet destination")

    web = cmd.add_parser("serve", help="Start the loopback-only local web workspace")
    web.add_argument("--profile", help="Path to local candidate preferences JSON")
    web.add_argument("--facts", help="Path to candidate-confirmed facts JSON")
    web.add_argument("--filings", help="Optional manually researched filing evidence CSV")
    web.add_argument("--packet-dir", default=".local/packets", help="Private packet storage")
    web.add_argument("--port", type=int, default=8765, help="Local HTTP port (default 8765)")

    listing = cmd.add_parser("list", help="List local jobs and application stages")
    listing.add_argument("--limit", type=int, default=100)
    return root


def _packet_destination(job_id: str, custom: str | None) -> Path:
    return Path(custom) if custom else Path(".local/packets") / f"{job_id}.json"


def _interactive_review(graph, packet: dict, thread_id: str) -> str:
    """Show complete application materials before a deliberate approval action."""
    from .graph_review import resume_review

    print(json.dumps({
        "job_id": packet["job_id"],
        "job_title": packet["job_title"],
        "employer": packet["employer"],
        "job_url": packet["job_url"],
        "resume_markdown": packet["resume_markdown"],
        "cover_note": packet["cover_note"],
        "sponsorship": packet["sponsorship"],
        "review_questions": packet["review_questions"],
        "packet_sha256": packet["packet_sha256"],
        "submission_sent": False,
    }, indent=2, ensure_ascii=False))
    answer = input(
        f"Type APPROVE {packet['job_id']} to mark this draft ready for "
        "MANUAL submission; otherwise press Enter to reject: "
    )
    status = resume_review(
        graph, packet, approved=(answer.strip() == f"APPROVE {packet['job_id']}"),
        thread_id=thread_id,
    )
    print(json.dumps({"review_status": status, "submission_sent": False}))
    return status


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from .dashboard import DashboardConfig, serve
            serve(
                DashboardConfig(
                    db_path=Path(args.db),
                    packet_dir=Path(args.packet_dir),
                    profile_path=Path(args.profile) if args.profile else None,
                    facts_path=Path(args.facts) if args.facts else None,
                    filings_path=Path(args.filings) if args.filings else None,
                ),
                port=args.port,
            )
            return 0
        jobs = None
        if args.command == "ingest":
            jobs = read_csv_jobs(args.csv)
        elif args.command == "discover":
            jobs = discover_jobspy(args.query, args.location, args.results)

        with Store(args.db) as store:
            if jobs is not None:
                added = store.add_jobs(jobs)
                print(json.dumps({
                    "imported": len(jobs),
                    "new": added,
                    "duplicates": len(jobs) - added,
                }))
            elif args.command == "rank":
                profile = CandidateProfile.load(args.profile)
                historical = load_historical_evidence(args.evidence)
                ranked = []
                for job in store.list_jobs(limit=10000):
                    fit = score_job(job, profile)
                    sponsor = assess_sponsorship(job, historical)
                    ranked.append({
                        "job_id": job.id, "company": job.company,
                        "title": job.title, "url": job.url,
                        "score": fit.score, "fit_reasons": fit.reasons,
                        "matched_skills": fit.matched_skills,
                        "candidate_skills_not_mentioned_in_posting": fit.candidate_skills_not_mentioned_in_posting,
                        "sponsorship_status": sponsor.status,
                        "sponsorship_reason": sponsor.reason,
                    })
                ranked.sort(key=lambda row: row["score"], reverse=True)
                print(json.dumps(ranked[:max(0, args.top)], indent=2))
            elif args.command == "sponsorship":
                job = store.get_job(args.job_id)
                print(json.dumps(sponsorship_report(
                    job, load_filing_evidence(args.filings)
                ), indent=2))
            elif args.command in {"draft", "workflow"}:
                job = store.get_job(args.job_id)
                if store.stage_of(job.id) == "submitted":
                    raise ValueError("Cannot regenerate a reviewed packet after submission is recorded")
                preferences = CandidateProfile.load(args.profile)
                bank = FactBank.load(args.facts)
                records = load_filing_evidence(args.filings)
                if args.command == "draft":
                    packet = build_packet(job, preferences, bank, records)
                else:
                    from .graph_review import build_career_graph

                    graph = build_career_graph()
                    thread_id = str(uuid.uuid4())
                    state = {
                        "job": asdict(job),
                        "preferences": asdict(preferences),
                        "fact_bank": json.loads(Path(args.facts).read_text(encoding="utf-8")),
                        "filing_records": [asdict(item) for item in records],
                    }
                    interrupted = graph.invoke(
                        state, config={"configurable": {"thread_id": thread_id}}
                    )
                    if "__interrupt__" not in interrupted:
                        raise RuntimeError("Graph did not stop for human review")
                    packet = interrupted["packet"]
                path = save_packet(packet, _packet_destination(job.id, args.output))
                print(json.dumps({
                    "packet_path": str(path),
                    "job_id": job.id,
                    "fit_score": packet["fit_score"],
                    "sponsorship_status": packet["sponsorship"]["status"],
                    "status": packet["status"],
                    "submission_sent": False,
                }, indent=2))
                if args.command == "workflow":
                    _interactive_review(graph, packet, thread_id)
            elif args.command == "review":
                from .graph_review import build_review_graph

                packet = load_packet(args.packet)
                graph = build_review_graph()
                thread_id = str(uuid.uuid4())
                interrupted = graph.invoke(
                    {"packet": packet}, config={"configurable": {"thread_id": thread_id}}
                )
                if "__interrupt__" not in interrupted:
                    raise RuntimeError("Graph did not stop for human review")
                _interactive_review(graph, packet, thread_id)
            elif args.command == "stage":
                store.stage(args.job_id)
                job = store.get_job(args.job_id)
                print(json.dumps({
                    "job_id": job.id, "stage": "ready_for_review",
                    "url": job.url,
                    "instruction": "Manually review and submit on employer site. Nothing has been sent.",
                }, indent=2))
            elif args.command == "confirm":
                store.confirm(args.job_id, args.receipt, attested=args.attest_confirmed)
                print(json.dumps({
                    "job_id": args.job_id, "stage": "submitted",
                    "recorded_by": "explicit_user_attestation",
                }))
            elif args.command == "list":
                print(json.dumps([
                    {
                        "job_id": job.id, "title": job.title,
                        "company": job.company, "url": job.url,
                        "stage": store.stage_of(job.id),
                    }
                    for job in store.list_jobs(limit=max(0, args.limit))
                ], indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
