# Career Agent Platform

**Privacy-first job intelligence and human-reviewed application workflow.**

Working name only. Final product branding awaits trademark clearance.

## What the MVP actually does

- Imports CSV job listings and deduplicates them by stable normalized URLs.
- Optionally discovers US jobs via JobSpy through an explicit CLI command or the dashboard search form.
- Offers an optional [browser companion](docs/BROWSER_COMPANION.md) to capture a selected job page for review before saving locally.
- Runs [saved JobSpy searches automatically and persists LangGraph draft/review workflows](docs/AUTOMATION.md), with restart recovery, run history and pause/resume controls.
- Scores title, verified profile skills, and location against job descriptions, with an explanation and candidate skills not mentioned in the posting; this is not a qualification-gap assessment.
- Flags apparent sponsorship exclusions, sponsorship claims and source-backed historical employer records separately. A past LCA is **not** an H-1B sponsorship offer.
- Stores jobs and application stages in local SQLite.
- Offers optional [AI requirement analysis and model routing](docs/AI_MODELS.md) for Anthropic, Azure OpenAI, AWS Bedrock, Ollama and LM Studio, with explicit data-sharing consent and evidence validation.
- Requires manual review and a user-attested employer confirmation reference before recording an application as submitted.
- Sends no applications, performs no browser automation, and uses no telemetry.

## Start your local workspace (Python 3.11+)

From the repository root, run:

```text
python scripts/launch.py
```

The launcher creates `.venv` if needed, installs the dashboard, JobSpy, résumé
and LCA import, encrypted backup and AI-provider dependencies using pip, then
starts the app. Package installation uses the network. For later starts use
`python scripts/launch.py --skip-install` to start without checking or
updating dependencies. Add `--with-pdf` when installing to also download the
Chromium PDF renderer; Linux may require additional system libraries. See
[local development](docs/LOCAL_DEVELOPMENT.md) for manual installation.

Open http://127.0.0.1:8765/ on this computer. If the port is occupied, add
`--port 8766` to the launcher command and open that port instead. Stop the server with Ctrl+C; keep it
running for scheduled searches. Your workspace uses `.local/career_agent.sqlite3`
and `.local/packets`, separate from the fictional demo in
[Local development](docs/LOCAL_DEVELOPMENT.md). These local files are plaintext;
never commit them, your résumé, or credentials to Git.
The default is a separate workspace from any custom `--db` previously used.
Use `--db PATH --packet-dir PATH` to select an existing workspace. Enabled saved
searches in the selected database may contact job boards when the server starts.

The **Get started** panel tracks profile setup and your first opportunity. Its
search button opens the JobSpy form; no search runs until you explicitly consent
and submit it. You can also add an opportunity manually.

1. Open **Career profile**, enter your goals and review extracted résumé lines.
   Save each accurate statement and then explicitly verify it. AI-suggested wording
   also needs your review and separate verification.
2. Start from **Home** to see profile readiness, saved roles and local follow-ups.
   On **Opportunity board**, use **Find jobs with JobSpy** or add a job manually.
   **Saved searches** can run while the server is running after you opt in. Source
   results can be incomplete. Review constraints and the original posting.
3. In **H-1B research**, download a supported DOL workbook or import a local DOL
   workbook/CSV or USCIS CSV, and associate an employer when needed. Historical filings
   are not a job-specific sponsorship commitment; unknowns require employer confirmation.
4. Open a role and use **Overview**, **Fit**, **Sponsorship**, and **Application**
   to review evidence and prepare a draft. Create and review a draft from verified facts. Approve the current version before
   exporting. Apply yourself on the employer site, then record your actual
   submission in the tracker. The app does not submit applications.

For PDF export, install the optional renderer and its browser:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[pdf]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

AI is optional. Set up a local or cloud route and test it with a synthetic prompt
as described in [AI models](docs/AI_MODELS.md); provider requests require consent
and may send the selected job and verified facts to that provider. For restorable,
passphrase-protected archives, see [backup and restore](docs/BACKUP.md). For a
separate fictional demo and test commands, see [Local development](docs/LOCAL_DEVELOPMENT.md).

## Design and future direction

See the [Product Requirements Document](docs/PRD.md) for the complete vision, feature catalog and release gates; then [Architecture](docs/ARCHITECTURE.md), [Roadmap](docs/ROADMAP.md), [Security](SECURITY.md), and [Open-source integration notes](docs/UPSTREAM.md) for the current implementation.

## Candidate intelligence and review (experimental)

Install the optional LangGraph runtime:

    .\.venv\Scripts\python.exe -m pip install -e ".[graph]"

For a separate fictional CLI demo, import examples and list their IDs:

    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 ingest examples/jobs.sample.csv
    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 list

Create a fact-grounded resume and cover-note draft:

    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 draft JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json --output .local/demo-packets/JOB_ID.json

Review the private demo packet using an actual LangGraph human interrupt:

    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 review .local/demo-packets/JOB_ID.json

Or run the full `analyze → draft → human_review` graph in one command:

    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 workflow JOB_ID --profile examples/profile.example.json --facts examples/candidate-facts.example.json --output .local/demo-packets/JOB_ID.json

The CLI prints the draft and requires you to type `APPROVE JOB_ID` before marking the packet approved **for manual submission only**. It does not apply to any jobs. The `workflow` command now persists SQLite checkpoints and accepts `--resume-run RUN_ID`; Enter leaves review pending. The standalone `review` utility remains in-memory. See [durable workflows](docs/AUTOMATION.md).

Optional source-backed sponsorship history:

    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 sponsorship JOB_ID --filings my-private-filings.csv

Find format, limitations, and privacy guidance in [Candidate workflow](docs/WORKFLOWS.md) and [Sponsorship methodology](docs/SPONSORSHIP.md). This CLI pipeline uses deterministic keyword heuristics; optional dashboard AI features are described in [AI models](docs/AI_MODELS.md). The dashboard supports reviewed résumé DOCX and [PDF export](docs/RESUME_PDF.md); PDF export requires optional dependencies. Supported DOL workbooks can be downloaded explicitly or imported locally. ATS form operations and application submission are not implemented.

Original source code license is not yet selected; public visibility does not grant reuse rights. Upstream projects retain their MIT licenses. Not employment or immigration legal advice.


## Local web dashboard (v0.3 preview)

A responsive local dashboard now provides a searchable opportunity board, transparent job-fit and sponsorship signals, previewable verified-fact application packets, review history, and manual-submission tracking.

Install the optional dependencies and start the local server:

    .\.venv\Scripts\python.exe -m pip install -e ".[web]"
    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 ingest examples/jobs.sample.csv
    .\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 serve --packet-dir .local/demo-packets --profile examples/profile.example.json --facts examples/candidate-facts.example.json

Open http://127.0.0.1:8765/ on the same computer. The demo candidate is fictional. The web app only binds to the local loopback address and requires a temporary session token for API calls. It has no user accounts or external submissions.

**Not production SaaS:** sensitive profiles and employer receipts remain in unencrypted local files/SQLite; do not run this outside a trusted local computer. See [Dashboard setup and security](docs/DASHBOARD.md).

To run the local workspace in a container with persistent storage, see [Docker setup](docs/DOCKER.md). Docker Desktop requires host networking to be enabled.


## Editable career profile (v0.4 preview)

Run the local dashboard without personal JSON files:

    .\.venv\Scripts\python.exe -m pip install -e ".[web]"
    .\.venv\Scripts\python.exe -m career_agent.cli serve

Open http://127.0.0.1:8765/ and select **Career profile**. Edit your preferences and save career facts. New or changed facts are **unverified** until you explicitly confirm them. Only verified facts can enter new application drafts. Changes are versioned in the local SQLite database; stale edits are rejected.

Existing --profile and --facts files are never modified automatically. If both are configured, the profile screen offers an explicit, one-time import into SQLite.

**Privacy:** local SQLite is not encrypted, and history retains earlier private notes and statements. Do not expose the localhost server or use it on a shared computer. See [Career Workspace](docs/CAREER_WORKSPACE.md).

 
## Local application CRM (v0.5 preview)

The **H-1B research** tab supports new/transfer preferences, a fiscal-year window, local DOL LCA CSV/XLSX imports, explicit downloads of supported DOL workbooks, company-record search and job-policy evidence. It also supports explicit [USCIS annual CSV refresh/import and live Greenhouse/Lever checks](docs/USCIS_LIVE_CHECKS.md). See [H-1B research](docs/H1B_RESEARCH.md). Company search uses the local index; filing history never establishes job availability or immigration eligibility.

Use the new **Application tracker** tab in the loopback dashboard to record interview stages, recruiter contacts, follow-up dates, and outcomes. Pre-application organizer stages never imply submission. The CRM does not send emails, reminders or applications.

    .\.venv\Scripts\python.exe -m pip install -e ".[web]"
    .\.venv\Scripts\python.exe -m career_agent.cli serve

See [Application CRM](docs/CRM.md) for the status model, available operations and privacy limitations.

Select **Download tracker history** to export all local tracker records and events as JSON, including private notes and submission references. See [Tracker export](docs/TRACKER_EXPORT.md) for the format, scope and privacy guidance.
