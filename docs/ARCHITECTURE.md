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