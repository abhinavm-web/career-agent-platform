# R1 Application CRM (F07)

The Career Agent application tracker keeps opportunities, recruiter contacts, interview stages and follow-up tasks together. It is a **local-only** workflow that never sends emails or submits applications.

## Start

    python -m pip install -e ".[web]"
    career-agent serve

Open http://127.0.0.1:8765/ on the same computer, then choose **Application tracker**. Add job postings on the Opportunity board before tracking them.

## Pipeline stages versus application submissions

A job starts as **discovered**. Before applying, it may be marked **saved**, **shortlisted** or **archived**. These are organizer labels only.

**Applied** appears only when the existing application workflow has recorded a manual submission with user attestation and employer reference. The CRM cannot manufacture an applied state. After a recorded application, candidates can record **screening**, **assessment**, **interview**, **offer**, **accepted**, **rejected** or **withdrawn** as appropriate. Terminal outcomes are intentionally locked in this MVP; an audited correction feature is future work.

Each manually recorded stage transition includes a timestamp, actor (candidate) and optional private note. Stage history is append-only. Pipeline stage is separate from application packet approval and the original submitted receipt.

## Recruiter contacts

For each opportunity, enter a recruiter's or referrer's name, optional role, email and contextual note. The current MVP allows adding and viewing contacts. The system never sends mail to them. Editing, removal and consent-based account integrations are future work.

## Follow-up tasks

Create a task title, due date (YYYY-MM-DD), and optionally link a contact associated with the same job. The summary lists upcoming and overdue tasks ordered by date. Open a job to reschedule or explicitly mark a task complete. Both actions leave a local event history.

Follow-up dates are **not automation schedules**. No email, reminder notification or calendar entry is created. Overdue detection uses the computer's local date.

## Privacy and reliability

This CRM stores names, emails, notes, statuses and tasks in the existing local SQLite database. **It is not encrypted and is not a cloud backup.** Do not run the loopback server on a shared or untrusted machine, expose it through a reverse proxy, or commit the database to Git.

Job descriptions and private contact notes are treated as untrusted data in the browser, rendered as text rather than executable HTML. All API endpoints use the same per-process session token and same-origin checks as the existing dashboard. This is not production authentication.

## Validation and acceptance criteria

- Pre-application stages cannot imply that a job was applied to.
- The application is considered submitted only through the previously tested manual attestation and receipt path.
- Each stage change leaves an event with the candidate actor and timestamp.
- Contacts and follow-ups cannot be silently attached to another job.
- Follow-ups cannot be completed twice or rescheduled after completion.
- No CRM operation sends external messages or application forms.
- Existing candidate profiles, reviewed packets and previously submitted applications remain readable.

Run the existing CI suite:

    python -m pip install -e ".[graph,web,dev]"
    python -m unittest discover -s tests -v
    node --check src/career_agent/web/app.js
    node --test tests/test_dashboard_js.cjs

The product vision and future requirements are in [PRD](PRD.md), feature **F07**.

## Later improvements

Editable contacts, opt-in reminders, interview notes and structured outcome reasons, event export/deletion, richer funnel analytics, and permitted email/calendar integrations. Any hosted edition requires encrypted storage, authentication, data governance and security review.
