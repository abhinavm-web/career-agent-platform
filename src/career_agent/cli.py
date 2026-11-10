"""CLI: discover, fit-score and manually confirm applications."""
from __future__ import annotations

import argparse
import json
import sys

from .discovery import discover_jobspy, read_csv_jobs
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

    listing = cmd.add_parser("list", help="List local jobs and application stages")
    listing.add_argument("--limit", type=int, default=100)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
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
                        "missing_skills": fit.missing_skills,
                        "sponsorship_status": sponsor.status,
                        "sponsorship_reason": sponsor.reason,
                    })
                ranked.sort(key=lambda row: row["score"], reverse=True)
                print(json.dumps(ranked[:max(0, args.top)], indent=2))
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
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
