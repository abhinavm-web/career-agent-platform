# Career Agent Platform

**Privacy-first job intelligence and human-reviewed application workflow.**

Working name only. Final product branding awaits trademark clearance.

## What the MVP actually does

- Imports CSV job listings and deduplicates them by stable normalized URLs.
- Optionally discovers US jobs via JobSpy when the user explicitly invokes a command.
- Scores title, verified profile skills, and location against job descriptions, with an explanation and candidate skills not mentioned in the posting; this is not a qualification-gap assessment.
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

See the [Product Requirements Document](docs/PRD.md) for the complete vision, feature catalog and release gates; then [Architecture](docs/ARCHITECTURE.md), [Roadmap](docs/ROADMAP.md), [Security](SECURITY.md), and [Open-source integration notes](docs/UPSTREAM.md) for the current implementation.

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


## Local web dashboard (v0.3 preview)

A responsive local dashboard now provides a searchable opportunity board, transparent job-fit and sponsorship signals, previewable verified-fact application packets, review history, and manual-submission tracking.

Install the optional dependencies and start the local server:

    python -m pip install -e ".[web]"
    career-agent ingest examples/jobs.sample.csv
    career-agent serve --profile examples/profile.example.json --facts examples/candidate-facts.example.json

Open http://127.0.0.1:8765/ on the same computer. The demo candidate is fictional. The web app only binds to the local loopback address and requires a temporary session token for API calls. It has no user accounts or external submissions.

**Not production SaaS:** sensitive profiles and employer receipts remain in unencrypted local files/SQLite; do not run this outside a trusted local computer. See [Dashboard setup and security](docs/DASHBOARD.md).


## Editable career profile (v0.4 preview)

Run the local dashboard without personal JSON files:

    python -m pip install -e ".[web]"
    career-agent serve

Open http://127.0.0.1:8765/ and select **Career profile**. Edit your preferences and save career facts. New or changed facts are **unverified** until you explicitly confirm them. Only verified facts can enter new application drafts. Changes are versioned in the local SQLite database; stale edits are rejected.

Existing --profile and --facts files are never modified automatically. If both are configured, the profile screen offers an explicit, one-time import into SQLite.

**Privacy:** local SQLite is not encrypted, and history retains earlier private notes and statements. Do not expose the localhost server or use it on a shared computer. See [Career Workspace](docs/CAREER_WORKSPACE.md).
