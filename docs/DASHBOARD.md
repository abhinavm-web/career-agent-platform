# Local web workspace

## Quick start

Install the optional web dependencies and keep the server bound to loopback:

    python -m pip install -e ".[web]"
    career-agent ingest examples/jobs.sample.csv
    career-agent serve --profile examples/profile.example.json --facts examples/candidate-facts.example.json

Open **http://127.0.0.1:8765/** in a browser on the same computer.

The default SQLite database is .local/career_agent.sqlite3 and draft packets are stored in .local/packets. Both locations are ignored by Git. You may customize storage:

    career-agent --db .local/my-jobs.sqlite3 serve --profile private/preferences.json --facts private/facts.json --filings private/employer-evidence.csv --packet-dir .local/my-packets --port 8765

All private candidate files should be outside the public repository or under a gitignored private/ directory. The two example JSON files contain fictional candidates, not your information.

## What works

- View up to 500 locally tracked jobs, search/filter by title/company, match relevance and sponsorship flags.
- Track a new job by pasting its published URL and details.
- Draft a Markdown resume and cover note containing **only candidate-attested statements**.
- Open a draft in the UI, inspect text and source-aware review checklist, then explicitly approve or reject.
- Approval/rejection is stored in a local append-only SQLite table with the packet fingerprint.
- After **manually applying on an employer site**, record a confirmation reference and attestation to update application status.
- Show dashboard counts and recent review decisions.

The app **does not** fetch jobs automatically, perform ATS submissions, answer visa screening questions, or transmit candidate data to a hosted API. The JobSpy adapter is a separate explicitly invoked CLI action. The dashboard never operates it in the background.

## Security and scope

This preview is **single-user local software**, not a hosted application. The server binds to 127.0.0.1 only. A random per-process session token protects all /api routes; browser same-origin, Host and content-type checks reduce unwanted requests. API responses disable caching, framing and third-party scripts. These are defense-in-depth measures, **not** identity management or production authentication.

Private profile, source notes and application drafts are stored in local plaintext files; employer confirmation references are stored in local SQLite. They are **not encrypted**. Use private filesystem permissions, avoid untrusted local users, and do not share the development server. A future cloud deployment requires actual authentication, user isolation, encrypted storage, CSRF auditing, retention/deletion, and a security review.

The dashboard records explicit review decisions independently of LangGraph. It does not pretend the previous in-memory workflow is durably resumable.

Reviews and application statuses are distinct: approving a packet means **ready for manual submission**. A job moves to "submitted" only after the person checks an explicit external-submission box and enters an employer receipt. The UI never sends an application.

## Tests

    python -m pip install -e ".[graph,web,dev]"
    python -m unittest discover -s tests -v
    node --check src/career_agent/web/app.js

## Roadmap

- Candidate profile editor with validation, safe data migration and private vault.
- Durable LangGraph checkpoints, verified sponsorship data ingestion and job enrichment.
- Resumes exported to DOCX/PDF with explicit user review.
- Supervised, permitted ATS adapters; no automatic submission without consent.
- Authenticated, encrypted multi-user deployment with audit logs.
