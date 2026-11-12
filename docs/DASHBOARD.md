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

- View locally tracked jobs in bounded batches, search/filter by title/company, match relevance and sponsorship flags.
- Track a new job by pasting its published URL and details.
- Draft a Markdown resume and cover note containing **only candidate-attested statements**.
- Open a draft in the UI, inspect text and source-aware review checklist, then explicitly approve or reject.
- New drafts include a fingerprinted list of selected verified facts, posting keyword matches and IDs of verified facts omitted by the 12-fact selection limit. The review panel shows those details without exposing private source notes. Older packets remain reviewable under their original fingerprints; regenerate to see the comparison.
- Download an approved current résumé as PDF or editable Word, and its cover note as UTF-8 plain text. Edits made after download are not tracked and are not covered by the packet approval; review those changes before use. Private career source notes are not included.
- Approval/rejection is stored in a local append-only SQLite table with the packet fingerprint.
- After **manually applying on an employer site**, record a confirmation reference and attestation to update application status.
- Show dashboard counts and recent review decisions.

JobSpy discovery is available through the CLI, the Opportunity board's **Find jobs with JobSpy** form, and opted-in recurring searches under **Saved searches**. Each dashboard search requires explicit acknowledgement that its query and location will be sent to external sources. Profile facts, recruiter contacts and private notes are not included in discovery requests. The app does not submit applications or answer visa screening questions.

To correct an outdated saved posting, open its details and choose **Correct saved description**. Enter the text you personally reviewed and optionally record a source note; both are stored locally as user-entered information, not independently verified. The previous and replacement descriptions are retained in local revision history. Each actual change invalidates existing drafts and approvals, even if later reverted. Regenerate and review a new packet. Jobs with a recorded submission remain locked. Existing unsaved editor text survives a board refresh; conflicting edits are rejected rather than overwriting newer corrections.

The opportunity board loads saved jobs in stable batches and can combine location, source, confirmed remote, sponsorship evidence, and posting observation filters over the loaded set. To bound browser memory, loading stops at 10,000 jobs or 32 Mi characters of JSON; a warning states when older jobs are excluded. Job additions during a load are included on the next refresh. The AI job picker also loads in batches, up to 10,000 jobs, and warns if older jobs are excluded. Other callers using the default jobs API receive at most 500 jobs per page. Open a job to explicitly check a supported Greenhouse or Lever posting. The saved observation shows its date and source; after 24 hours, or if its date is invalid or in the future, availability is treated as unknown. A listed observation does not guarantee that the employer is hiring or offers sponsorship, and no live check runs merely by loading the board.

New JobSpy discoveries also retain the latest source-reported posting date for each job and source when supplied as an unambiguous calendar date. Missing or conflicting dates are disclosed; if duplicate results disagree or omit a date, the display does not silently present a single certain value. Future dates are flagged against the UTC calendar date. Dates from earlier searches are not retained, and observations created before this feature have no posting date. These are provider reports, not verified original publication dates or evidence that a role is currently open. They do not change ranking, application packets or review approvals.

**Compare requirements** in the job detail reads recognized posting headings, including plain and common Markdown headings, and displays the original heading and bullets alongside potentially relevant verified facts using shared keywords. Indented continuation lines are included in a bullet excerpt; other wrapped text may still be omitted. Generic **Qualifications** and **Basic Qualifications** headings have an unspecified requirement level. Candidate-related sections such as **About You** and **What You'll Bring** are displayed as context, not classified as required or preferred qualifications. It is a local, deterministic aid: it does not alter the ranking, send data to an AI model, decide eligibility, or establish that a missing keyword is a qualification gap. Unlabeled prose, unsupported formatting, long bullets and requirements beyond the display limit require manual review of the original posting. Private source notes are not returned.

**Build preparation guide** on an opportunity creates local practice prompts on demand using the same saved posting excerpts and verified-fact keyword suggestions. It displays the source heading, posting and profile revisions, and up to two candidate facts per excerpt; additional matches and omitted excerpts are disclosed. General prompts remain available when no recognized section is found. These are not real employer interview questions, model-generated answers or proof of fit. Nothing is saved or sent externally; regenerate after changing the posting or profile and choose only examples you can substantiate.

Install discovery with `python -m pip install -e ".[web,jobspy]"`, restart the server, then enter a role, location and requested results per source (1–100). Choose at least one of Indeed, LinkedIn and ZipRecruiter for the manual US search; changing the query, location, result count or sources clears consent so you can review the new request. Returned jobs are normalized and deduplicated by the existing job store. Manual searches run in an isolated worker with a two-minute deadline; a timeout or server shutdown stops the worker without importing partial results. Saved automatic searches have separate source selection, stricter limits and failure backoff; see [Automation](AUTOMATION.md).

Job sources may block requests, rate-limit or return partial results. Zero results do not prove that no jobs exist. Missing dependencies and provider exceptions show retryable errors without returning upstream exception details. The test suite uses mocked discovery, not live job-board requests; source-specific live validation remains outstanding. Respect each provider's terms and rate limits.

## Security and scope

This preview is **single-user local software**, not a hosted application. The server binds to 127.0.0.1 only. A random per-process session token protects all /api routes; browser same-origin, Host and content-type checks reduce unwanted requests. API responses disable caching, framing and third-party scripts. These are defense-in-depth measures, **not** identity management or production authentication.

Private profile, source notes and application drafts are stored in local plaintext files; employer confirmation references are stored in local SQLite. They are **not encrypted**. Use private filesystem permissions, avoid untrusted local users, and do not share the development server. A future cloud deployment requires actual authentication, user isolation, encrypted storage, CSRF auditing, retention/deletion, and a security review.

New dashboard drafts and review decisions advance durable LangGraph runs. Legacy packets retain their existing direct-review path. Input hashes, packet fingerprints and candidate revisions prevent stale approval; resumable runs and status history appear in the automation panel.

For a tailored draft, choose up to 40 verified facts and arrange the order of résumé sections and facts within each section. The automatic draft still selects at most 12. Facts remain verbatim and traceable to the selected IDs; to change wording, edit and reverify the fact in Career profile. Creating a new layout creates a new fingerprint and requires another review. Empty sections do not appear in the exported résumé. This is a structured layout editor, not a free-form document editor.

For durable workflows, review, export and manual-submission recording recheck configured profile, fact and filing inputs as well as workspace and posting revisions. Missing or changed inputs require a fresh draft. Legacy JSON and CSV files are external to SQLite and cannot be locked by the app against another program changing them; import the career profile into the workspace for transactional revision checks. Standalone legacy packets created without a workflow input snapshot cannot be checked against their original external inputs.

New tailored packets provide a side-by-side comparison of the **verified fact bank at drafting time** with the selected facts in résumé order, including fact IDs and omitted markers. This is not the originally uploaded résumé, which the app does not retain as a baseline. The comparison snapshot contains all verified statements at drafting time and is stored in the private fingerprinted packet; private source notes are excluded. Omitted statements do not appear in the résumé, cover note, DOCX/PDF export, or tracker export. Existing packets are not changed and do not acquire a comparison snapshot.

Reviews and application statuses are distinct: approving a packet means **ready for manual submission**. A job moves to "submitted" only after the person checks an explicit external-submission box and enters an employer receipt. The UI never sends an application.

## Tests

    python -m pip install -e ".[graph,web,dev]"
    python -m unittest discover -s tests -v
    node --check src/career_agent/web/app.js

## Roadmap

- Candidate profile editor with validation, safe data migration and private vault.
- Broader agent workflows, additional source connectors and stronger job enrichment; core durable checkpoints and DOL/USCIS imports are implemented.
- More résumé layouts and in-app document editing with renewed review.
- Supervised, permitted ATS adapters; no automatic submission without consent.
- Authenticated, encrypted multi-user deployment with audit logs.


## Editable candidate profile (v0.4)

The local dashboard now supports a **Career profile** screen. Start with no additional arguments: career-agent serve. Enter name, headline, target roles, skills, preferred locations and remote preference. Add, edit, verify, revoke or remove career statements. Only explicitly verified facts may appear in newly generated application packets.

The profile and all revisions are stored as **unencrypted plaintext** in the existing SQLite file, not in the public repository. Source notes remain private to the local workspace. Edits to a confirmed claim automatically revoke verification. Requests include a revision token, with stale edits rejected as HTTP 409.

An explicit one-time import can copy existing configured --profile and --facts files into the workspace, without altering them. See [Career Workspace](CAREER_WORKSPACE.md).

**Safety invariant:** A new candidate-workspace revision invalidates previous draft review approvals. Outdated packets are displayed with a warning and cannot be approved or recorded as newly submitted until regenerated and reviewed again.
