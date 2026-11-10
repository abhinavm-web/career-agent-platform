# Roadmap

## Phase 1 — MVP foundation

- [x] Local CSV ingestion, URL normalization and deduplication
- [x] Optional JobSpy discovery
- [x] Explicit dashboard JobSpy search, import counts and duplicate handling
- [x] Explainable matching and skills-gap report
- [x] Conservative sponsorship evidence assessment
- [x] Manual review and attested submission tracking
- [x] Offline regression tests and public-safe sample data
- [ ] Live job-board adapter integration tests (requires network and provider-terms review)

## Phase 2 — Candidate intelligence

- [x] Local candidate-attested fact bank with private source notes
- [x] Deterministic role-specific resume and cover-note draft packets
- [x] Reviewed résumé PDF export using adapted Career Ops styling and optional Chromium
- [x] Version fingerprint and interactive approval of a packet
- [x] Source-linked, dated manually curated H-1B employer history
- [x] LangGraph analyze → draft → human-review interrupt (in-memory prototype)

- [ ] Encrypted verified candidate profile
- [ ] Seniority, location, compensation and work-authorization constraints
- [ ] Direct employer posting checks and provenance timestamps
- [ ] Truthful, user-reviewed ATS résumé tailoring
- [ ] DOL/USCIS-based historical sponsorship analysis with documented limitations

## Phase 3 — Agent orchestration

- [ ] LangGraph typed state and checkpointing
- [ ] Human interrupt before application transmission
- [ ] Multi-source discovery, retry and duplicate clustering
- [ ] Supervised ATS connectors for Greenhouse, Lever and Ashby
- [ ] Submission receipt audit trail and funnel analytics

## Phase 3.5 — Single-user local dashboard

- [x] Responsive browser-based opportunity board with search and filters
- [x] Local token-protected API and explicit loopback binding
- [x] Candidate-confirmed draft viewing, approval and rejection
- [x] Append-only local review audit events and employer-submission attestations
- [x] Contract/security tests for local HTTP endpoints
- [ ] Client-side accessibility audit, usability sessions and cross-browser testing
- [x] Local candidate-profile editor, explicit fact verification and SQLite revision history (unencrypted)
- [ ] Encrypted private vault, backups and safe data migration

## R1 — Application CRM increment (F07)

- [x] Separate organizer and post-application stage timelines with constrained transitions
- [x] Local recruiter contact records and optional contextual notes
- [x] Due-date follow-ups, rescheduling, completion and event history
- [x] Application tracker tab and due-follow-up summary
- [x] API and database regression coverage for local candidate-controlled actions
- [x] Editable contacts with preserved follow-up links
- [x] Tracker search, stage and priority filters, and priority change history
- [x] Dated, append-only interview notes
- [x] Recorded submission/interview/offer/acceptance counts and explicit-denominator rates
- [ ] Calendar notifications, source-specific analytics and history exports

## Phase 4 — Product

- [x] Local-only web dashboard and review queue (non-hosted MVP)
- [ ] Production-ready hosted web dashboard
- [ ] User accounts, consent, deletion and encryption
- [ ] Secure hosted operations, retention and access controls

Success metric: qualified recruiter conversations and interviews, not number of applications sent.
