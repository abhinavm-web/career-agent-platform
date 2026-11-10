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

## Intended LangGraph implementation (not built yet)

A future orchestration layer will add typed state and checkpoints for discover, dedupe, enrich, sponsorship research, score, truthful résumé tailoring, review and tracking. An interrupt must require candidate authorization immediately before any external submission. LangGraph is an architectural direction, not part of the current MVP.

## Trust boundaries

Job descriptions and scraped websites are untrusted input, never instructions. Profiles should contain verified facts only. No code runs from job descriptions. External sending is not implemented. Historical DOL LCA filings do not guarantee H-1B sponsorship for any particular role.

Before hosting the service, add identity management, encryption, explicit consent, audit trails, retention and deletion.

## Phase 2: evidence-grounded application drafts and LangGraph

- `candidate.py`: local candidate-confirmed fact bank; reject unverified statements.
- `application.py`: select and format verbatim supported facts, construct fingerprinted local packets.
- `sponsor_evidence.py`: dated employer-source records, exact entity matching, explicit limitations.
- `graph_review.py`: optional LangGraph analyze → draft → interrupt → resume. The graph cannot send an application.

Both the standard `draft` command and the optional `workflow` graph write application packets to a gitignored local directory. An approval only authorizes manual submission by the candidate. These in-memory checkpoints are not persistent and must not be treated as hosted-production security or auditing.


## Phase 3.5: local-only HTTP dashboard

- dashboard.py: optional FastAPI local API, session-token checks, same-origin/Host checks, manual job entry and review operations.
- web/: packaged accessible HTML, locally served CSS and JavaScript; user-derived text only inserted with textContent.
- storage.py: append-only packet_reviews table, keyed by job ID and the current packet fingerprint.
- CLI serve: binds to 127.0.0.1 and does not accept a public host override.

The server does not invoke JobSpy, LangGraph, external LLMs, or ATS websites. Its local web reviews are persisted independently of the in-memory LangGraph graph. The server never submits an application, and a submitted status requires explicit user attestation of a real external submission.

This architecture is only for a trusted single user. API session tokens are not replacement identity or user isolation in hosted multi-user deployments. See [Dashboard](DASHBOARD.md).
