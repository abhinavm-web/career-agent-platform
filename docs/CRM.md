# R1 Application CRM (F07)

The Career Agent application tracker keeps opportunities, recruiter contacts, interview stages and follow-up tasks together. It is a **local-only** workflow that never sends emails or submits applications.

## Start

    python -m pip install -e ".[web]"
    career-agent serve

Open http://127.0.0.1:8765/ on the same computer, then choose **Application tracker**. Add job postings on the Opportunity board before tracking them.

The tracker lists stored opportunities independently of the board's browser loading limits. Older opportunities and their follow-ups remain accessible for stage changes, contact management and task completion.

## Pipeline stages versus application submissions

A job starts as **discovered**. Before applying, it may be marked **saved**, **shortlisted** or **archived**. These are organizer labels only.

**Applied** appears only when the existing application workflow has recorded a manual submission with user attestation and employer reference. The CRM cannot manufacture an applied state. After a recorded application, candidates can record **screening**, **assessment**, **interview**, **offer**, **accepted**, **rejected** or **withdrawn** as appropriate.

To fix a terminal outcome entered in error, open the opportunity, enter a reason and explicitly confirm **Correct mistaken outcome**. This restores the immediately preceding post-submission stage (or applied) and records a new correction linked to the original event. You can then record the actual stage. The original outcome, correction reason, actor and time remain visible in history; a stale correction is rejected. The independent submission record and receipt are unchanged. This corrects an erroneous entry, not an employer's subsequent reversal of a genuine outcome.

Each manually recorded stage transition includes a timestamp, actor (candidate) and optional private note. Stage history is append-only. Pipeline stage is separate from application packet approval and the original submitted receipt.

## Find and prioritize opportunities

Use the tracker's search field for title, company or location, then combine it with pipeline-stage and priority filters. **Clear filters** restores the full list. The result count describes the filtered list; pipeline totals and upcoming follow-ups continue to cover the whole workspace. Filters last for the current page session.

Select an opportunity and save a **high**, **medium**, **low** or **no priority** label. Priority changes retain a candidate-attributed, timestamped history and do not change the application stage or submission receipt.

## Interview notes

Record an interview date, round/topic and private notes on an opportunity. Notes are displayed newest interview date first, with the recording timestamp and candidate attribution. Notes can also capture preparation for a future interview. They do not assert submission, schedule events or advance the pipeline.

Saved notes are append-only in this version: add another note for a correction. They remain in unencrypted local SQLite, like contacts and other CRM history. Note editing and deletion are not yet implemented; the tracker-history export includes the recorded notes.

## Recruiter contacts

For each opportunity, enter a recruiter's or referrer's name, optional role, email and contextual note. Choose **Edit contact** to correct these fields, then **Save contact**, or **Cancel** to discard edits. Linked follow-up tasks retain the same contact and display its updated name. Contact edits replace the current fields; this MVP does not keep a contact revision history. **Remove contact** requires confirmation and detaches the contact from linked follow-ups while retaining the tasks and their histories. Deleting a contact does not erase copies in earlier backups, free pages or SQLite remnants; names manually included in other notes or task titles also remain. The system never sends mail to contacts. Consent-based account integrations are future work.

## Follow-up tasks

Create a task title, due date (YYYY-MM-DD), and optionally link a contact associated with the same job. The summary shows the first six open tasks and the total. Choose **Browse all open follow-ups** for dated pages, and open a job or use the page actions to reschedule, cancel, or explicitly mark a task complete. A job’s task history uses pages of 100, including completed and cancelled tasks; switching those pages discards unsaved tracker form edits. Lists can shift when tasks are added, completed, cancelled or rescheduled, so refresh when needed. Task actions refresh the tracker and return the job history to its first page. Cancellation removes an unnecessary task from the pending list without claiming that it was completed. These actions leave a local event history; cancelled and completed tasks cannot be changed.

Follow-up dates are **not automation schedules**. No email, reminder notification or calendar entry is created. Overdue detection uses the computer's local date.

The tracker also highlights up to 20 attested applications in applied, screening, assessment or interview stages with at least 14 UTC calendar days since the last recorded active stage. The submission attestation date is used until a post-submission stage is recorded. Applications with an open follow-up task are omitted, as are offers and terminal outcomes. This is a prompt to review your records, not evidence that an employer has not replied: email, calls and activity outside the tracker are not checked. Selecting a suggestion opens the opportunity; no task or message is created. Invalid or future timestamps do not produce suggestions.

## Recorded application outcomes

The tracker shows all-time counts of attested submissions, opportunities with an explicitly recorded interview, recorded offers, and accepted offers. Each opportunity counts once per milestone, including when multiple interview rounds are recorded. Later rejection or withdrawal does not erase an earlier interview or offer.

Interview and offer rates divide their milestone counts by all attested submissions, rounded to one decimal place. With no submissions, rates are unavailable. Drafts, approvals, and interview notes never count as submissions or interview milestones. Skipped pipeline stages are not inferred: moving straight to an offer does not create a recorded interview.

A terminal event explicitly corrected as erroneous is excluded from milestone counts; genuine earlier interviews and offers remain counted.

These are candidate-entered records, not independent employer verification, hiring predictions or causal measures. Search and list filters do not change the all-time metrics.

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

Opt-in reminders, structured outcome reasons, event export/deletion, richer funnel analytics, and permitted email/calendar integrations. Any hosted edition requires encrypted storage, authentication, data governance and security review.
