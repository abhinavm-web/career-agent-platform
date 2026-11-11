# Architecture

## Local MVP flow

    CSV ───────────────┐
                        ├──> Normalizer + dedupe ──> SQLite
    Optional JobSpy ───┘                                |
                                                         +--> Explainable fit scorer
    Local JSON candidate preferences ────────────────────+
    Source-backed employer-history CSV ──────────────────+
                                                         |
                                                         +--> Stage for review
                                                                 |
                                                         Manual candidate review
                                                                 |
                                                         Manual employer submission
                                                                 |
                                                   Explicit attestation + receipt
                                                                 |
                                                      Record as submitted

## Modules

- models.py: normalized job URLs and candidate-profile preferences.
- discovery.py: CSV importer and optional job-board adapter.
- scoring.py: title, skill and location relevance, not a hiring prediction.
- sponsorship.py: explicit exclusions, positive claims, historical evidence and unknown status.
- storage.py: local database and safe application state transitions.
- cli.py: manually invoked commands.

## Implemented core LangGraph orchestration

`automation.py` runs scheduled discover → normalize/dedupe → import graphs. `workflows.py` manages analyze → sponsorship → draft → validate → review graphs. Both use synchronous SQLite checkpoints. Application reviews resume after restart; discovery retries are safe through deduplication and transactional import receipts. Additional follow-up/interview agents and external submission remain unimplemented. See [operational details](AUTOMATION.md).

## Trust boundaries

Job descriptions and scraped websites are untrusted input, never instructions. Profiles should contain verified facts only. No code runs from job descriptions. External sending is not implemented. Historical DOL LCA filings do not guarantee H-1B sponsorship for any particular role.

Before hosting the service, add identity management, encryption, explicit consent, audit trails, retention and deletion.

## Phase 2: evidence-grounded application drafts and LangGraph

- `candidate.py`: local candidate-confirmed fact bank; reject unverified statements.
- `application.py`: select and format verbatim supported facts, construct fingerprinted local packets.
- `sponsor_evidence.py`: dated employer-source records, exact entity matching, explicit limitations.
- `graph_review.py`: optional LangGraph analyze → draft → interrupt → resume. The graph cannot send an application.

Both the standard `draft` command and the `workflow` graph write application packets to a gitignored local directory. An approval only authorizes manual submission by the candidate. The dashboard and CLI `workflow` use durable local SQLite checkpoints; the standalone `review` command remains an in-memory utility. This is not hosted-production security.


## Phase 3.5: local-only HTTP dashboard

- dashboard.py: optional FastAPI local API, session-token checks, same-origin/Host checks, manual job entry and review operations.
- web/: packaged accessible HTML, locally served CSS and JavaScript; user-derived text only inserted with textContent.
- storage.py: append-only packet_reviews table, keyed by job ID and the current packet fingerprint.
- CLI serve: binds to 127.0.0.1 and does not accept a public host override.

The server invokes JobSpy through explicit searches or saved opt-in schedules, LangGraph for durable draft/review workflows, selected model providers after per-request consent, and supported ATS APIs after explicit live-check requests. The server never submits an application, and submitted status requires explicit user attestation of a real external submission.

This architecture is only for a trusted single user. API session tokens are not replacement identity or user isolation in hosted multi-user deployments. See [Dashboard](DASHBOARD.md).


## R1: Versioned local candidate workspace

- workspace.py stores private profile/fact snapshots as sequential SQLite revisions and validates each update.
- The dashboard exposes GET/PUT /api/workspace, POST /api/workspace/facts/{id}/verification, GET /api/workspace/history, and POST /api/workspace/import for explicit legacy migration.
- Before an editable workspace is initialized, the dashboard continues using read-only --profile/--facts configuration. After initialization, dashboard matching and drafts use the saved workspace.
- A separately acknowledged verification step is mandatory for newly added or edited career facts. Only verified facts are converted into FactBank objects for drafting.
- Existing CLI workflows supplied with --profile and --facts are unchanged. Nothing in this increment adds application sending or multi-user hosting.

This is a single-user, local, **unencrypted** revision log, not a secure production vault. See [Career Workspace](CAREER_WORKSPACE.md).


## R1 — Application CRM (F07)

- crm.py: isolated local SQLite tables for pipeline stage events, recruiter contacts, follow-up tasks and follow-up event history.
- dashboard.py: API routes for CRM summary, per-job details, stage changes, contacts, follow-up creation, rescheduling and completion.
- web/: local Application tracker view with event timeline and manually initiated actions.
- A job's applied state is **derived only** from the existing Store-confirmed manual submission. CRM pipeline events cannot set it. Contacts, notes and follow-up tasks do not trigger email, ATS or calendar actions.

CRM is single-user plaintext SQLite like the rest of the MVP, not a hosted service. See [CRM](CRM.md) for workflow and limitations.
