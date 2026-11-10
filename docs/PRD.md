# Product Requirements Document: Career Agent Platform

**Document version:** 1.0 (product intent and implementation baseline)
**Last updated:** November 10, 2026
**Product name:** Career Agent Platform (working name only; final brand requires trademark/domain clearance)
**Audience:** Product owners, design, engineers, QA, coding agents, security reviewers, and potential contributors
**Status:** Living PRD. This is the destination and scope reference, **not authorization to implement every feature in one change**.

> **Agent reading order:** Read the root AGENTS.md first, then this PRD to understand *why and what* we are building. Consult docs/ARCHITECTURE.md, docs/ROADMAP.md, docs/DASHBOARD.md, docs/UPSTREAM.md, SECURITY.md, and the relevant code to understand *how and what currently exists*. Follow the current issue or user request for the scope of any particular PR. When a request conflicts with the PRD or security rules, make the conflict explicit before acting.

## 1. Executive vision and end goal

Build an **AI-powered career intelligence and opportunity-management platform** that helps people find high-quality roles, understand their actual fit, present their verified experience effectively, navigate employer-specific constraints, and manage the process from discovery to offer.

The long-term product is an **intelligent career operating system**, not another job scraper, résumé template generator, or mass-application bot.

The product should unify the strengths of three categories of existing tools:

1. **Job discovery:** Broad, timely, lawful sourcing and normalization (inspired by JobSpy).
2. **Career operations:** Relevant matching, honest career positioning, résumé and cover-letter preparation, and application tracking (inspired by Career Ops).
3. **Application assistance:** Form preparation, review queues, workflow automation, and deliberate human oversight (inspired by JobAppAgent).

We should build an **original product with modular integrations**, not copy/paste three separate repositories. Their licenses, dependencies, and service-provider terms must be reviewed before importing code. See docs/UPSTREAM.md.

### End-state experience

A job seeker should be able to describe their career goals once, maintain a trusted career profile, and receive a continuously updated, explainable shortlist of opportunities. For each role, the platform should show *why it fits*, *what is unknown*, *what the employer says about sponsorship*, and *which verified facts support the application*. The user can then review and approve individualized documents, submit through a supported and authorized process or manually, and track outcomes through interviews and offers.

The core promise is **better career decisions and more qualified conversations with employers**, not the largest number of applications.

## 2. Product principles and non-negotiable boundaries

| Principle | Required behavior |
| --- | --- |
| Quality over volume | Optimize for relevant opportunities, recruiter conversations, interviews, and outcomes; do not reward indiscriminate submissions. |
| Candidate control | The candidate owns the profile, decides what is shared, and approves each application or outreach action before it leaves the system. |
| Truthfulness by design | No invented titles, employers, dates, degrees, credentials, experience, metrics, work authorization, or achievements. Drafts must be traceable to candidate-confirmed facts. |
| Explainability | Show evidence and uncertainty behind rankings, role constraints, and sponsorship conclusions. A heuristic score is not a prediction of being hired. |
| Evidence provenance | Preserve original job URLs, source, observed time, employer legal name when available, and evidence dates. Separate source text from model inference. |
| Privacy first | Do not commit candidate data or secrets to the public repository. Default to private local storage in the MVP; hosted processing is a separately reviewed milestone. |
| Human review for consequential actions | Never silently apply, send recruiter messages, answer legal/authorization questions, purchase services, or change external accounts. |
| Safe, legal integrations | Prefer approved APIs and official career pages; comply with provider terms, rate limits, and applicable law. No CAPTCHA or access-control bypasses. |
| Incremental delivery | Ship narrowly scoped, testable vertical slices; avoid speculative frameworks and claims that roadmap work is already live. |

**Automation permission rule:** Discovery and analysis may become opt-in automated background tasks. External application submission and recruiter outreach remain human-authorized on a per-action basis. Current code does **not** submit or send anything. Any future supervised submission capability requires separate product, security, and compliance approval; this PRD alone is not permission to implement it.

## 3. Users, jobs to be done, and initial market

### Primary users

| Persona | Pain point | Desired outcome |
| --- | --- | --- |
| Experienced professional / AI leader | Broad titles, unclear expectations, inconsistent sourcing, manual tailoring | A prioritized set of genuinely suitable roles with strong positioning and transparent reasoning |
| H-1B or other sponsorship-dependent U.S. applicant | Employer sponsorship practices are unclear, historical records are easily misread | Source-backed sponsorship signals, explicit unknowns, and efficient targeting without false promises |
| Career changer / adjacent-role applicant | Transferable experience is hard to articulate without overclaiming | Honest skill mapping and explainable transferable-experience narratives |
| Active job seeker managing multiple pipelines | Job listings, emails, applications, interview notes, and follow-ups are scattered | A single reliable source of truth for each opportunity and its next action |

### Future, not initial release scope

Career coaches, placement organizations, university career centers, and recruiters may become **separate opt-in product personas**. Never reuse or sell an individual's private profile to employers/recruiters merely because it exists in the candidate workspace.

### Initial launch assumptions (to validate)

- **Market:** United States first; international markets later via country-specific job boards, labor rules, languages, and sponsorship models.
- **Initial user:** Individual candidate, one private workspace, human-reviewed applications.
- **Differentiator:** Integrated job relevance + verified career facts + defensible sponsorship intelligence + supervised workflow + outcome learning.
- **Commercial packaging:** Undecided. Do not assume a subscription price, a business model, or a company name is approved.

## 4. User journey: from goal to offer

| Step | Candidate action | Platform responsibility | User-visible result |
| --- | --- | --- | --- |
| 1. Onboard | Define target roles, seniority, location, compensation, remote preference, and sponsorship needs | Capture preferences privately and distinguish hard requirements from soft preferences | Personal search strategy |
| 2. Establish facts | Enter, import, validate, and explicitly confirm experience, projects, skills, credentials, and results | Build an evidence-linked, versioned career fact bank; flag uncertain claims | Trusted candidate profile |
| 3. Discover | Run a search or enable a permitted scheduled discovery task | Ingest authorized sources, normalize records, deduplicate, check freshness | Searchable opportunity inventory |
| 4. Qualify | Review the shortlist | Explain role relevance, actual unmet requirements where evidence supports them, constraints, and missing data | Ranked opportunities with reason codes |
| 5. Research employer | Inspect job details and sponsorship context | Show posting policy, official employer statements, dated historical filings, and conflict/unknown warnings | Evidence panel, not a visa guarantee |
| 6. Prepare | Request role-specific materials | Use only confirmed candidate facts; expose exact changes and evidence links | Draft résumé, cover note, and optional outreach |
| 7. Review | Verify every statement and screening answer | Require an explicit decision against the current draft version | Approved, rejected, or needs-edit packet |
| 8. Apply | Manually submit or use a separately authorized supported integration (future) | Record what happened; do not silently send | Verified/attested submission event and evidence |
| 9. Progress | Record responses, interviews, feedback, follow-ups | Track pipeline, dates, tasks, and approved reminders | Opportunity timeline and next actions |
| 10. Learn | Evaluate actual outcomes | Improve preference weights from user-controlled feedback without inventing causal claims | Better future shortlist and progress insights |

## 5. What exists today versus what the PRD proposes

**Status meanings:** **Current** = implemented in the main-branch v0.3 local MVP per repository documentation; **Next** = near-term product requirement; **Later** = planned but not committed; **Exploratory** = vision requiring explicit decisions.

| Area | Current baseline (v0.3, local) | Target direction |
| --- | --- | --- |
| Job discovery | CSV import, URL normalization/deduplication; optional CLI-invoked JobSpy | Multi-source compliant discovery, employer career pages, freshness and filtering |
| Matching | Deterministic title / candidate-skill mentions / location heuristic | Structured hard constraints, explainable relevance, verified requirement-to-evidence matching |
| Sponsorship | Posting keyword flags and manually supplied dated employer records | Official-source ingestion, company identity resolution, job-specific evidence and clearly labeled uncertainty |
| Candidate profile | Local JSON candidate preferences and confirmed fact bank | Private editable, versioned, permissioned career vault |
| Application materials | Verbatim-fact Markdown résumé / cover-note packet | ATS-friendly tailored drafts, tracked edits and DOCX/PDF exports |
| Agent workflow | LangGraph analyze → draft → review with in-memory checkpointing | Durable, auditable orchestration with retries, checkpoints and scoped agent tools |
| Application state | Manual review decisions, local append-only review events, user-attested submissions | Full pipeline from saved to interview/offer, consent and receipts |
| UI | Loopback-only FastAPI dashboard with job board, packet review, audit events | Full accessible user workspace, document studio and outcome analytics |
| Security | Single-user local app, session-token API; private files/SQLite are not encrypted | Authenticated, isolated, encrypted multi-user product if hosting is approved |
| Application sending | **None** | Only separately reviewed, legally permitted, user-authorized supervised integrations |

**Do not change the above Current column to describe an unmerged branch, mocked integration, or unfinished experiment as released.** Update it when implementation is merged and verified.

## 6. Detailed feature requirements

Priority key: **P0** = necessary for a useful trustworthy candidate product; **P1** = important after core quality is demonstrated; **P2** = expansion; **Explore** = requires a product decision. These are priorities, not a blanket instruction to implement them.

### F01 — Candidate identity, preferences, and verified career vault (P0)

**Problem:** A résumé alone is not a reliable, reusable store of achievements, context, preferences, and evidence.

**Requirements**
- Capture role families, target titles, seniority, work arrangement, US locations, optional relocation, compensation bands, industry preferences, and **sponsorship-related search preferences**.
- Separate hard constraints (e.g., must support a specific work arrangement) from ranking preferences (e.g., preferred industry).
- Import existing résumés and professional history only after informed consent. Suggest structured facts; **do not mark machine-extracted facts verified** until the person confirms them.
- Store every employment, project, skills, achievement, education, and certification claim with a stable fact ID, candidate verification state, version, date, and optional private source reference.
- Enable correction, deletion, revocation of a previously confirmed claim, and export of the candidate's own data.
- Treat government identifiers and sensitive immigration documents as a separate high-security category; do not require or upload them for basic matching.

**Acceptance criteria**
- An unverified/inconsistent fact is not silently placed in outward-facing application material.
- A generated claim can be traced to one or more candidate-verified fact IDs; edits invalidate an earlier review.
- The candidate can update preferences and facts without modifying public repository files.

**Today:** JSON preferences and confirmed fact-bank parsing exist. Secure editing, import review, version history, and encryption are **not built**.

### F02 — Multi-source opportunity discovery and quality control (P0)

**Requirements**
- Discover roles through permitted company career sites, structured ATS endpoints/API integrations, and authorized aggregator adapters.
- Initial US sources may include JobSpy-supported sources where their current terms permit use; prospective direct ATS adapters include Greenhouse, Lever and Ashby, subject to available published interfaces and approval.
- Capture title, company display and legal identity when known, original source URL, location, remote/hybrid/on-site, employment type, salary when stated, description, posting identifier, first-seen and last-checked timestamps.
- Normalize and deduplicate across sites without stripping job-identifying URL parameters or merging different roles at the same company.
- Mark roles as active, stale, expired, or unverified based on reliable evidence; show the reason and when checked.
- Support explicit user-driven search and later an **opt-in** scheduled refresh with source-level rate limits and backoff. Do not bypass CAPTCHA or access controls.
- Support favorites, exclusions, saved queries, and human corrections to metadata.

**Acceptance criteria**
- Every opportunity links back to an inspectable original source.
- Duplicate records merge carefully while source provenance remains inspectable.
- Failed discovery is labeled as a failure, not interpreted as zero open jobs; sources may be incomplete.
- Disabled/suspended source adapters do not crash the rest of the product.

**Today:** CSV normalization and an optional CLI JobSpy adapter exist; the dashboard does not scrape or schedule jobs.

### F03 — Candidate-to-role match engine (P0)

**Requirements**
- Separate **hard-constraint eligibility checks**, **soft relevance ranking**, and **uncertainty**.
- Identify job responsibilities, required versus preferred qualifications, location/work arrangement, seniority, compensation requirements, and hiring restrictions with provenance.
- Match extracted requirements to *verified candidate evidence*, then label supported, possibly transferable, unverified, or apparently missing; never treat a candidate skill **not mentioned** in a posting as a skill deficit.
- Explain why a role was prioritized and let a candidate contest a requirement or change a preference.
- Handle ambiguous or incomplete descriptions with "unknown" rather than a false negative.
- Support semantic matching as an optional, testable enhancement. A model score must not masquerade as an employer's hiring probability or an ATS score.

**Acceptance criteria**
- For every shortlisted role, show the main match reasons, constraints, and unknowns.
- A comparison like "candidate has Python; posting asks for Rust" must not incorrectly label Python as a missing candidate qualification.
- Ranking changes are testable with fixed fixtures, and the user can see why a role moved.

**Today:** Title overlap, candidate skill mentions, and location yield a heuristic score; no true qualification-gap inference exists.

### F04 — Visa and work-authorization sponsorship intelligence (P0 differentiator)

**Requirements**
- Keep at least four clearly separated signals: **explicit job-level exclusion**, **posting appears to allow sponsorship**, **historical employer filing evidence only**, and **unknown**. If evidence conflicts, show the conflict, sources, and need to verify directly.
- Ingest source-backed employer history from official US DOL/USCIS information where lawful and technically supported, preserving record type, fiscal year, filing status and exact employer entity.
- Do not imply an LCA equals an approved petition, an offer to sponsor, H-1B portability eligibility, or immigration advice.
- Resolve employer aliases/subsidiaries cautiously. A matching brand is not proof that an affiliate or specific office supports sponsorship.
- Surface wording from current job postings and official employer policies with capture date. Support candidate notes from direct recruiter confirmation, labeled by source.
- Maintain privacy by not needing immigration case numbers, passport images, or other sensitive documents to rank jobs.
- Let users search by sponsorship policy without claiming exhaustive verification or guaranteeing results.

**Acceptance criteria**
- Every historical sponsorship record is attributed and dated; unverified user-imported records are labeled accordingly.
- Unknown sponsorship stays **unknown**, never "no" or "yes" by inference.
- Any purported job-specific positive claim requires subsequent verification for that role; screening answers remain candidate-controlled.

**Today:** Keyword checks and manually curated CSV history exist; official bulk-data ingestion and independent validation do not.

### F05 — Truthful résumé, cover-letter, and outreach studio (P0/P1)

**Requirements**
- Generate an application-specific **draft** using only the candidate's verified facts, appropriate job details, and user-selected tone/format.
- Preserve factual statements. Propose rewording only if a semantic-fidelity check and candidate approval maintain truth; never fabricate quantitative impacts.
- Clearly distinguish "verified fact", "suggested wording", "unverified inference", and "needs user input".
- Highlight additions, omissions, reordered achievements, and tradeoffs versus the approved base résumé.
- Provide clean, accessible, ATS-friendly formatting with configurable sections and truthful date/credential handling.
- Export to Markdown in the near term; DOCX/PDF export is a subsequent requirement subject to formatting and file-inspection tests.
- Generate recruiter outreach drafts and interview introductions without sending automatically.

**Acceptance criteria**
- Every outward-facing substantive claim is supported by a candidate-confirmed fact or user-entered final edit.
- Reviewers can compare original and tailored versions and approve a specific immutable draft version.
- An unverified career claim cannot pass review simply because an LLM wrote it convincingly.
- Source notes and private evidence must not leak into outward-facing documents.

**Today:** Verbatim-fact Markdown packets, cover notes, fact IDs and SHA-256 version checks exist. Advanced rewriting, diff UI, and DOCX/PDF exports are not implemented.

### F06 — Application preparation, approvals, and submission control (P0/P1)

**Requirements**
- A role-specific application packet includes job snapshot, verified résumé draft, optional cover letter, supporting fact IDs, unanswered questions, sponsorship caveats, and a version/fingerprint.
- Review flow distinguishes **drafted → needs changes → approved for manual submission → externally submitted/attested**. "Approved" must never be displayed as "submitted".
- Provide side-by-side review of documents and any draft responses to standard forms.
- Treat work-authorization, citizenship, disability, demographic disclosures, compensation expectations and legal declarations as sensitive questions to be answered explicitly by the applicant, not guessed by an agent.
- Current release uses **manual external submission** only. If future ATS assistance is approved, require a per-application authorization, final preview, compliant integration path, and verifiable receipt. CAPTCHA, terms, or required human authentication must never be bypassed.
- Disallow overwriting a submitted packet; retain its reviewed, submitted version. A regenerated packet needs a fresh approval before any use.

**Acceptance criteria**
- No background or silent application transmissions occur.
- Approval of packet A cannot approve changed packet B or a different job.
- A job becomes "submitted" only on a separately supported, verified receipt event or a clear manual attestation that the user already submitted externally.
- Post-submission application materials and decision history are preserved.

**Today:** Local drafts, version-bound review approvals, append-only review events, and manual receipt attestations exist; no external ATS submission exists.

### F07 — Application CRM, pipeline, and next actions (P0/P1)

**Requirements**
- Track a consistent event timeline: discovered, saved, shortlisted, drafting, review, approved, applied, screening, assessment, interview stages, offer, accepted, rejected, withdrawn, or archived.
- Keep **pipeline status** separate from **agent task state**, **document approval**, and **external submission receipt**.
- Allow manual updates and optional import of approved correspondence/calendar events later, with user-controlled connections.
- Support contact names, follow-up dates, interview notes, referral introductions, reasons for rejection, and priority tags.
- Suggest follow-ups based on elapsed time but do not invent responses or send messages.
- Detect duplicates and avoid creating multiple applications for the same employer opening unless the user explicitly chooses otherwise.

**Acceptance criteria**
- Each state change has actor, time, role ID, and provenance, with a recoverable history.
- The user can find stale applications, active interviews and next actions quickly.
- A drafted or approved application is not counted as submitted.

**Today:** Basic local states, review events and attested submissions exist. Full CRM states, contact management and reminders are planned.

### F08 — Multi-agent orchestration and tool gateway (P1)

**Agent responsibilities**

| Agent or service | Inputs | Outputs | Permission boundary |
| --- | --- | --- | --- |
| Discovery | User-approved filters, enabled sources | Candidate job records and source metadata | Read permitted job sources only |
| Normalization / deduplication | Raw job records | Canonical job entities with provenance | Local transformation |
| Requirement analysis / matching | Job description, verified candidate facts | Evidence-grounded fit, constraints, unknowns | No invented qualifications |
| Sponsorship research | Employer/job identity and cited sources | Dated and caveated policy/history report | Research only; not legal advice |
| Document preparation | Approved facts, job snapshot, style request | Versioned résumé and note drafts | No transmission or unapproved factual invention |
| Application preparation | Approved packet and user-provided form responses | Review queue and submission checklist | Cannot submit without separately approved workflow |
| Follow-up assistant | User-tracked pipeline and approved contact data | Suggested reminder or message draft | Cannot send autonomously |
| Interview assistant | Job, candidate facts, selected interview stage | Preparation guide, practice questions | No fabricated interview intelligence |
| Quality / policy evaluator | Proposed outputs, citations and action plan | Validation failures, risk flags, approval blockers | Can block; cannot bypass required human approval |

**Engineering requirements**
- Use typed LangGraph state where an orchestrated workflow provides tangible benefit; prefer direct functions for simple single-step operations.
- Separate analysis, draft preparation, review, and external actions as distinct nodes/tool scopes.
- Add durable checkpoints, retries, idempotency, timeout handling, traceability, and explicit consent before background execution.
- Treat job postings, emails, web pages, and résumé uploads as untrusted content. Protect prompts and tool permissions from injection.
- Store model provider, prompt/template version, provenance and reviewer decisions without including secrets or unnecessary applicant PII in logs.
- Support at least a deterministic offline pathway when third-party models are unavailable.

**Acceptance criteria**
- A paused review can resume with the same packet and an authenticated actor in a future durable design.
- A stale approval or change in source material causes re-review.
- Model/tool failures cannot trigger unauthorized external actions or fabricate successful job applications.
- Agent jobs can be cancelled and retried safely.

**Today:** Optional LangGraph analyze → draft → review exists with **in-memory** checkpointing only. The local dashboard's persisted review events are distinct and are not a durable LangGraph checkpoint implementation.

### F09 — Candidate web workspace and UX (P0/P1)

**Required screens**
1. **Onboarding:** explain privacy, configure job preferences, review source and sponsor limits.
2. **Career profile:** inspect and confirm individual facts; manage résumé versions and private evidence.
3. **Opportunity board:** saved searches, filters, ranking, freshness and sponsorship indicators.
4. **Opportunity detail:** original posting, requirements, employer evidence, fit explanation and uncertainty.
5. **Document studio:** role-specific draft, fact citations, difference view and exports.
6. **Review center:** pending decisions, exact packet version, blocking questions and approvals.
7. **Application tracker:** status, contacts, follow-up, interviews, outcomes and activity timeline.
8. **Account/data controls:** consent, source connections, data export/deletion and (when hosted) account security.

**UX requirements**
- Desktop-first for initial professional workflow, responsive on mobile.
- Keyboard navigation, semantic controls, readable error messages, accessible contrast, and screen-reader labels.
- Explain "what should I do next?" on every primary screen.
- Show unknown/stale/unverified information explicitly rather than visually implying confidence.
- Provide a short first-use experience with fictional examples; do not mix demo data with real applicant records.

**Today:** A local responsive job board, detail panel, packet previews, reviews, and history are implemented. Onboarding and full document studio are planned.

### F10 — Outcome analytics and continuous learning (P1)

**Requirements**
- Candidate-controlled funnel: relevant roles found → saved → drafted → approved → applied → replies → screens → interviews → offers.
- Break down performance by source, role family, seniority, employer type, application version, and sponsorship-status evidence, with privacy safeguards.
- Distinguish user-recorded outcomes from inferred/proxy outcomes.
- Capture corrections ("bad match", "not actually sponsoring", "wrong seniority") to improve future recommendations.
- Avoid claiming causation or score calibration from tiny datasets. Do not train on private applicant data without explicit, revocable permission.

**Acceptance criteria**
- Dashboard counts agree with underlying event records.
- Quality metrics cannot be inflated by drafts or approvals being mislabeled as submissions.
- Users can export their own raw history and explanations.

### F11 — Integrations, ecosystem, and data portability (P1/P2)

**Requirements**
- Pluggable discovery adapters with documented source policies, credentials handling, rate limits, and test fixtures.
- First-party candidate import/export: JSON/CSV initially; structured résumé and DOCX/PDF later.
- Potential connectors: official ATS sources, calendar/email, employer careers, approved job boards, identity systems and model providers.
- External accounts use user-initiated consent, least-privilege access and revocation.
- No dependence on any one LLM vendor or a requirement to self-host models.
- Review legal implications and licenses before bundling upstream components.

**Acceptance criteria**
- Disabling one provider preserves local job history and does not block other functions.
- No private third-party account is accessed before user consent.
- Exports contain meaningful provenance and can be deleted from the product on request.

### F12 — Privacy, security, reliability and governance (cross-cutting P0)

**Requirements**
- Keep real candidate data out of Git and public fixtures. Classify profiles, résumés, notes, documents, tokens and immigration-sensitive information appropriately.
- Local release: loopback-only service and explicitly documented plaintext-at-rest limitation. Hosted release: authenticated users, tenant separation, encryption, audited consent, secure secret storage, export and deletion.
- Never render untrusted job descriptions as executable HTML or agent instructions. Apply sanitization, input validation, source boundaries and injection-resistance tests.
- Maintain append-only review/application evidence. Protect submitted documents from overwriting.
- Log only necessary operational metadata, redact secrets and sensitive data, and avoid unauthorized telemetry.
- Publish terms, retention rules, data-use policy, and accessible support processes before commercial launch.

**Acceptance criteria**
- Security regressions cover cross-user access (once hosted), stale packet review, unsafe URLs, cross-origin attacks, malicious job text, unsafe file paths and unauthorized submissions.
- A security/privacy review gates any hosted deployment or new external-write integration.
- Local-only limitations are visible to users and developers.

## 7. Application and agent state model

Separate state machines to avoid conflating preparatory and external actions:

| State family | Candidate states | Who may advance it |
| --- | --- | --- |
| Job opportunity | discovered, saved, shortlisted, dismissed, expired | User and source-verified discovery tasks |
| Document packet | draft, needs_revision, awaiting_review, approved, rejected, superseded | Drafting system creates; candidate approves/rejects; version changes supersede |
| Application | not_applied, manual_submission_attested, submitted_verified, screening, interviewing, offered, rejected, withdrawn | User and explicitly authorized evidence-backed integrations |
| Agent run | queued, running, waiting_for_human, completed, failed, cancelled | Orchestrator and authorized user |
| Sponsorship evidence | explicit_exclusion, posting_claims_sponsorship, historical_only, unknown, conflicting | Evidence processing, with user-visible provenance |

**Invariant:** Document approval is not application submission. The submitted packet must remain immutable or have an auditable preserved version. Source changes invalidate unsupported inferences, not already-recorded historical facts.

## 8. Key data entities and contracts

- **CandidateProfile:** internal user identity, target-role preferences, private consent and work-location constraints.
- **CareerFact:** fact ID, text, type, candidate verification state, source note/reference, version and timestamp.
- **Employer:** display and legal entities, aliases, location, sector, verified identifiers, and identity confidence.
- **JobPosting:** canonical ID, source URLs, employer, text snapshot, requirements, status, observed dates and content hash.
- **MatchAssessment:** job/candidate version, score components, hard-constraint evaluation, supported evidence IDs, gaps and unknowns.
- **SponsorshipEvidence:** exact job/employer scope, source document URL, fiscal year, case type/status, captured date, verification state and limitations.
- **ApplicationPacket:** job snapshot, selected verified fact IDs, generated documents, unanswered questions, version hash and review status.
- **ReviewDecision:** packet hash, authenticated actor (when available), explicit decision, time, reason and source context.
- **ApplicationEvent:** event type, job, actor, timestamp, employer receipt/source and outcome.
- **AgentRun:** run ID, node/state, input version, model/tool versions, checkpoints, retries, approval interrupt and audit metadata.
- **Reminder / Contact (later):** candidate-controlled outreach or interview data with explicit consent to integrate external accounts.

**Rules:** Stable identifiers, provenance, separation between public job data and private candidate data, explainable transformations, migration/version strategy, and no silent destructive updates.

## 9. System architecture and deployment evolution

**Current local architecture:** Python 3.11+, SQLite, CLI, optional JobSpy, optional LangGraph, FastAPI/Uvicorn, packaged vanilla web UI. Local files store facts and draft packets. The current local app is **not** a secured hosted product.

**Target logical layers:** User workspace → user preferences and verified fact vault → job/source ingestion → normalized job store → match and sponsorship intelligence → document studio → orchestrated workflow with approval interrupts → tracking/analytics → approved external integrations.

**Deployment stages**
- **Local MVP:** Single trusted user, loopback-only, private local storage, explicit manual review.
- **Secure personal beta:** Proper private vault and migrations, dependable background tasks, versioned evidence and role-specific exports. Hosting decision still open.
- **Hosted multi-user SaaS (conditional):** Authentication, tenant isolation, encryption, access policies, consent management, rate limits, secured event processing, monitoring, deletion, incident handling and commercial policies.
- **Enterprise/B2B (exploratory):** Opt-in career-coach/recruiter workspaces with separate sharing permissions and governance; never implicit transfer of candidate data.

**Technology choices:** Prefer managed services when hosting is approved; avoid a vendor lock-in commitment now. LangGraph is a recommended orchestration mechanism for multi-step, interruptible workflows, not a mandate to put every function into an agent.

## 10. Non-functional requirements

These are **proposed targets for later release gates, not measured current performance**.

| Category | Proposed requirement |
| --- | --- |
| Correctness | Candidate statements in generated materials always correspond to verified user facts; no unreviewed changes masquerade as approved |
| Explainability | Every match/sponsorship result has an evidence/uncertainty explanation |
| Safety | Zero unauthorized application submissions or outbound recruiter messages |
| Reliability | Agent retries are idempotent; a crash cannot convert a draft into a submitted application |
| Data integrity | Versioned reviewed packets remain retrievable after edits, status changes or later drafts |
| Security | No secrets or real applicant data in public Git; externally writable actions scoped to explicit user consent |
| Performance | Set concrete UI/query SLOs after benchmarking realistic datasets and target environments |
| Accessibility | Aim for WCAG 2.2 AA on primary workflows; validate with automated and manual accessibility tests |
| Portability | Core workflow can run without an external LLM or a specific third-party provider |
| Observability | Track operational errors and workflow outcomes without leaking applicant data into logs |

## 11. Success metrics and anti-metrics

**North-star candidate outcome:** Qualified employer conversations and interview opportunities attributable to applications the user chose to pursue.

**Leading metrics:** Relevant roles accepted/saved per session, precision of top recommendations from human feedback, percent of documents passing factual review, sponsorship-source coverage and freshness, time to prepare a reviewed packet, recruiter-response rate, interviews per *verified submitted* application, and retention of active users.

**Integrity metrics:** Unsupported factual claims (target zero), unauthorized sends (target zero), stale approvals accepted (target zero), private data leaked into public repos/logs (target zero), incorrect sponsorship guarantees (target zero).

**Anti-metrics:** Number of jobs scraped, raw application count, number of AI agents, generated words, and estimated ATS "percentages" are not measures of career success by themselves.

Do not report fabricated usage, conversion, revenue or TAM numbers. Establish baselines only from consensually collected and clearly defined production data.

## 12. Release sequence, milestones, and acceptance gates

| Release | Objective | Representative features | Gate |
| --- | --- | --- | --- |
| **R0: Foundation — current** | Operable local prototype | CSV/optional JobSpy, heuristic fit, source-backed manual sponsorship, fact-based drafts, LangGraph review demo, local web board, append-only review and manual submission records | Existing repository CI; clearly labeled limitations |
| **R1: Trusted candidate workspace — next** | Real candidate can safely operate end-to-end without editing code | Private profile editor, candidate fact verification, accurate job/requirement provenance, richer CRM statuses, safe backups and draft versioning | Usability and privacy review; no fabricated claims or unauthorized actions |
| **R2: Higher-quality intelligence** | More relevant and timely shortlists with defensible sponsorship evidence | Compliant ATS/official-source connectors, posting freshness, structured matching, government-data import with provenance, human correction loops | Source-specific integration tests, false-positive review, transparent confidence |
| **R3: Document and workflow maturity** | Reduce genuine application preparation effort | Verified résumé tailoring, DOCX/PDF export, version diff, durable LangGraph, notifications and follow-up suggestions | Snapshot/export QA, replay/idempotency tests, explicit approvals |
| **R4: Supervised application support (conditional)** | Assist permitted external form workflows without forfeiting user control | ATS integrations and assisted form entry where permitted, per-job preview and approval, receipts | Security/compliance approval, provider policy review, tested consent and rollback paths |
| **R5: Commercial product (conditional)** | Secure scalable multi-user service | Accounts, encryption, tenant isolation, subscriptions if decided, data portability, support/operations | Independent security/privacy assessment, legal review, product-market validation |

**Sequencing is directional, not a deadline or blanket engineering commitment.** A specific milestone is done only when it has measurable acceptance criteria, tests, documented limitations and appropriate user approval.

## 13. Cross-cutting quality assurance and Definition of Done

For each issue or PR:

1. State scope, assumptions, and a short plan. Identify the exact PRD requirement IDs affected.
2. Confirm whether implementation already exists. Prefer modifying the smallest relevant module.
3. Define the expected observable behavior and counterexamples before coding.
4. Add or update offline unit/regression tests. For adapters, add contract fixtures and negative cases. For UI, include interaction and accessibility checks.
5. Test failure, stale data, duplicate, untrusted content, privacy and explicit-consent paths.
6. Verify both Python and web checks when relevant, and review the final diff for unrelated modifications.
7. Update documentation only where actual behavior or product understanding changed.
8. Do not declare a planned feature "shipped" without verified integration and merged code.
9. Record unresolved risks, migration impacts, dependencies, and user decisions.
10. Keep secrets, private résumés, applicant information and real immigration documents out of test fixtures and public artifacts.

**Existing test commands:** python -m unittest discover -s tests -v; node --check src/career_agent/web/app.js; node --test tests/test_dashboard_js.cjs. Install optional dependencies only when the relevant tests require them.

## 14. Explicitly out of scope / prohibited without new approval

- Uncontrolled mass application submission, credential sharing, CAPTCHA bypass, and attempts to evade job-board policies.
- Synthetic or exaggerated job history, credentials, accomplishments or work-authorization claims.
- A claim that an employer will sponsor a job based solely on prior LCAs or petitions.
- Silent sending of emails, recruiter DMs, or form submissions.
- Selling, sharing or training on candidate data without meaningful user permission.
- A publicly deployed server using today's local-only session-token protections.
- Building a recruiter marketplace or immigration legal-advice service as part of the initial candidate MVP.
- Committing to a trademarked product name, monetization model or open-source license without an explicit owner decision.

## 15. Open decisions and dependencies

| Decision | Why it matters | Current position |
| --- | --- | --- |
| Final brand and domains | Legal risk and go-to-market identity | Unresolved; Career Agent Platform is a working name |
| Original-code license | Contribution, distribution and commercial strategy | Repository owner has not selected one |
| Data-hosting model | Privacy/security architecture and operating costs | Local-only currently; hosted service requires separate approval |
| Approved source connectors | Terms, accuracy, rate limits and coverage | Review each source independently |
| Country/visa expansion | Very different regulatory and data constraints | US/H-1B-focused initial research; international later |
| LLM choice and AI budget | Cost, privacy, quality and deployment | Provider-neutral goal; no mandatory paid model in MVP |
| Application automation boundary | Material legal, privacy, and abuse risk | No autonomous sending; future supervised support conditional |
| Pricing and B2B roadmap | PMF and privacy model | Research later, not locked |
| Outcome benchmarks | Honest measurement of product value | No validated production baselines yet |

## 16. How coding agents should use this PRD

- **Do not build the whole document at once.** This PRD describes the destination; the current task determines the authorized slice.
- For a new request, identify the relevant F01–F12 features and whether the desired result is **Current**, **Next**, **Later**, or **Exploratory**.
- Read the code and current documentation first. Propose a minimal implementation with explicit success criteria.
- Treat the safety and privacy invariants as higher-priority than speculative feature convenience.
- Prefer clear deterministic implementations until a particular workflow benefits from an LLM/agent.
- For ambiguous user priorities or major changes (hosting, agent-triggered sends, data sharing, licensing), surface the decision and do not silently assume consent.
- Return actual verification evidence: test outcome, PR link, unmerged status, blockers and what is still not implemented.
- Update this PRD only when the product decision changes; do not automatically expand scope whenever an implementation detail changes.

**End goal:** A trusted, evidence-driven, candidate-controlled career operating system that materially improves the quality of opportunities pursued and the outcomes of the job search.
