# Career Agent Platform

**Privacy-first job intelligence and human-reviewed application workflow.**

Working name only. Final product branding awaits trademark clearance.

## What the MVP actually does

- Imports CSV job listings and deduplicates them by stable normalized URLs.
- Optionally discovers US jobs via JobSpy when the user explicitly invokes a command.
- Scores title, verified profile skills, and location against job descriptions, with an explanation and missing skills.
- Flags apparent sponsorship exclusions, sponsorship claims and source-backed historical employer records separately. A past LCA is **not** an H-1B sponsorship offer.
- Stores jobs and application stages in local SQLite.
- Requires manual review and a user-attested employer confirmation reference before recording an application as submitted.
- Sends no applications, performs no browser automation, and uses no telemetry.

## Setup (Python 3.11+)

Clone this repo, open its root folder, then run:

    python -m pip install -e .
    career-agent ingest examples/jobs.sample.csv
    career-agent rank --profile examples/profile.example.json
    career-agent list

Application review:

    career-agent stage JOB_ID

Review the role, your actual experience, every application field, and your resume. Submit yourself on the employer site. Only after an actual employer confirmation:

    career-agent confirm JOB_ID --receipt YOUR_CONFIRMATION_REFERENCE --attest-confirmed

A user-entered receipt is an attestation, not independent validation.

Optional live job discovery:

    python -m pip install -e ".[jobspy]"
    career-agent discover --query "AI Transformation Manager" --location "United States" --results 20

This performs network access only when you run it. Respect job-board rules and rate limits.

Testing:

    python -m unittest discover -s tests -v

Local state defaults to .local/career_agent.sqlite3 and is gitignored. Sample jobs and candidate preferences are fictional; don't commit real resumes, profiles or credentials.

## Design and future direction

See [Architecture](docs/ARCHITECTURE.md), [Roadmap](docs/ROADMAP.md), [Security](SECURITY.md), and [Open-source integration notes](docs/UPSTREAM.md).

## Candidate intelligence and review (experimental)

Install the optional LangGraph runtime:

    python -m pip install -e ".[graph]"

After importing example jobs, use `career-agent list` to copy a job ID.

Create a fact-grounded resume and cover-note draft:

    career-agent draft JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json

Review the private packet under `.local/packets/JOB_ID.json` using an actual LangGraph human interrupt:

    career-agent review .local/packets/JOB_ID.json

Or run the full `analyze → draft → human_review` graph in one command:

    career-agent workflow JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json

The CLI prints the draft and requires you to type `APPROVE JOB_ID` before marking the packet approved **for manual submission only**. It does not apply to any jobs. The graph is an in-memory local demonstration, not a persistent cloud service.

Optional source-backed sponsorship history:

    career-agent sponsorship JOB_ID --filings my-private-filings.csv

Find format, limitations, and privacy guidance in [Candidate workflow](docs/WORKFLOWS.md) and [Sponsorship methodology](docs/SPONSORSHIP.md). This pipeline uses deterministic keyword heuristics, not an external LLM. It does not generate PDF or DOCX resumes, download DOL workbooks, operate ATS sites, or submit applications.

Original source code license is not yet selected; public visibility does not grant reuse rights. Upstream projects retain their MIT licenses. Not employment or immigration legal advice.
