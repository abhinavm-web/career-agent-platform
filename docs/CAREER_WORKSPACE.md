# R1: Local candidate career workspace

## Purpose

A usable, private-by-default local editor for a candidate's preferences, career facts, and supporting notes. This is the first F01 vertical slice from the [PRD](PRD.md). **It is not the final encrypted candidate vault.**

The existing personal profiles and confirmed fact banks were read-only JSON files passed to the CLI. The dashboard now lets users edit preferences and enter career statements without modifying those external files or the public Git repository.

## Getting started

Install optional dashboard dependencies and start the server:

```bash
python -m pip install -e ".[web]"
career-agent serve
```

Open http://127.0.0.1:8765/ and choose **Career profile**. Enter name, professional headline, target roles, skills, and locations. Add a career statement with a private source note, save, then use **Verify this fact** only after you have checked it.

Optionally mark fully remote work as required and list exact employer names to exclude. An opportunity's **Your hard constraints** panel is separate from the keyword preference score and does not hide or reject jobs. Simple, explicitly labeled work-arrangement lines are quoted; a hybrid or on-site label is flagged, including when the job source also marks it remote. Multiple or qualified labels remain unknown. A source-marked remote role still needs geographic confirmation; an unmarked one is unknown, not necessarily on-site. Employer exclusions match the saved display name after case and whitespace normalization; aliases and affiliated legal entities remain unknown. Edit your preferences to correct them.

Optionally set a minimum annual base salary in USD. The app compares it only with a single, clearly labeled annual USD base salary line in the saved posting and quotes that line. Other formats, currencies, multiple pay amounts and missing data remain unknown. A range reaching your minimum does not promise an offer or establish eligibility for a particular pay band. Verify all compensation with the employer; pay observations do not change the match score or automatically hide jobs.

You can also enter preferred title-level phrases such as `Director`, `VP`, or `Head of`. The opportunity detail shows the saved job title and which exact phrases occur, ignoring case. A missing phrase remains unknown: titles and organizations use different labels, and a phrase match does not establish seniority, eligibility, or suitability. This preference is displayed separately and does not change the score or hide jobs.

New or modified facts are *not verified*. A separate explicit acknowledgement is necessary to mark each statement verified. **Only verified facts can enter generated résumés or cover notes.** Modifying a statement, category, keywords, or private evidence note revokes that verification until you acknowledge the revised statement again.

You may optionally enter a single résumé contact line, such as your email, phone and professional profile URL. It is copied into newly generated résumés and their reviewed fingerprint; check it before approving or exporting. It remains in local unencrypted workspace revisions, workflow checkpoints and saved packets, and may persist in backups and historical drafts after it is changed or cleared. The separate contact field is not sent with AI requirement analysis, wording suggestions or career chat, though text you explicitly enter into an AI prompt or verified fact can itself include personal information. Previously generated drafts do not change automatically; regenerate and review after updating the line.

The current source note is only returned inside the local authenticated workspace. It is never inserted into the outward-facing draft.

You can paste résumé text or extract it locally from a PDF, DOCX, or UTF-8 text file up to 2 MB (install `.[resume]` for PDF support). Extraction is limited to 30 PDF pages and 20,000 text characters; scanned images require OCR, which is not included. No file is sent to an AI provider or stored by this extraction. Find fact suggestions to group lines under recognizable experience, projects, skills, education, and certifications headings. These categories follow the most recent recognized heading and are not verified classifications; unrecognized headings may cause a later line to have the wrong category. Lines before any recognized heading are unclassified. Choose a line for review in the career fact editor and correct its category or wording. Selecting a line does not save or verify it. Review the statement, add a private evidence note, save it, and verify it separately when accurate.

For faster setup, expand **Stage several statements for review** after finding suggestions. Select up to 30 distinct lines, choose a category for unclassified lines, and provide a private source note shared by the selected statements. One local workspace save stages them as **unverified**, with no inferred keywords; duplicate or already-saved statements must be deselected. The profile holds at most 100 facts. Review each statement, category and evidence note, add useful keywords, and verify it separately before use in drafts. The original résumé text is not saved by this action, though selected statements and source notes become part of the local unencrypted workspace and its revision history.

The career assistant uses a configured AI route. Each request requires consent and sends the question and current session's conversation to that route; an optional checkbox also shares saved, verified fact statements, which may contain personal information. Private source notes and unverified facts are not sent separately. Up to six exchanges remain in the browser until cleared or reloaded; they are not persisted. Switching providers sends the conversation to the newly selected provider on the next consented request. Answers are unverified suggestions and never change the profile. A local model server can itself forward requests, and cloud providers may charge for use.

## Migrating existing profile files

If you already use --profile and --facts to launch the dashboard, those files remain read-only. When no editable workspace exists yet, the Career profile screen offers **Import configured files**. This copies the existing candidate-confirmed facts into SQLite after explicit acknowledgement. It leaves your original files untouched. The app refuses to overwrite a previously initialized workspace with a second import.

For safe experimentation, use the fictional examples:

```bash
career-agent serve --profile examples/profile.example.json --facts examples/candidate-facts.example.json
```

These examples are not real career claims.

## Model and history

- One local SQLite database stores the latest document plus append-only revisions. Revision 0 means "not initialized".
- Each revision includes a complete profile and fact snapshot, a sequential version ID, timestamp and edit/verification event type.
- A save must include the exact expected revision. A concurrent/stale save receives HTTP 409, so it cannot silently overwrite newer changes.
- New or edited statements are never implicitly verified, even if a client submits a true flag. The separate verification endpoint requires explicit acknowledgement.
- Existing verified, unmodified facts remain verified when unrelated preferences change. Deleted/revoked facts are excluded from new application drafts.
- The dashboard reads these revisions after initialization. Existing command-line workflows using --profile / --facts continue to read the specified files.

## Security and privacy limitations

This preview is a *single-user local application*. Data remains in plaintext SQLite, including previous revisions, private notes, and previously verified statements. It is not encrypted, and there is no user identity, tenant isolation, deletion/export guarantee, or cloud backup. Protect your computer and filesystem, and never commit the SQLite file or real candidate facts to Git.

Candidate claims cannot be independently verified by the software. Clicking Verify is your explicit assertion of accuracy. A verified fact may still be misleading if you entered it incorrectly; reviewing a final application draft is required.

The local dashboard stays on 127.0.0.1 and uses the same session token/origin restrictions as the previous version. This is not permission to deploy a publicly accessible server.

## Tests and acceptance criteria

- A saved profile can be edited without changing source JSON files.
- New/edited facts remain unverified; only an explicit verification action marks them verified.
- A revoked/deleted/unverified fact does not enter a generated application packet.
- Stale revision edits are rejected and previous revisions remain queryable in the history list.
- No personal information is added to repo fixtures, logs, or public documents.
- Existing job board, review approvals and manually attested submissions behave as before.

Install test extras and run: `python -m pip install -e ".[graph,web,dev]"`, then `python -m unittest discover -s tests -v`, `node --check src/career_agent/web/app.js`, and `node --test tests/test_dashboard_js.cjs`.

## Still out of scope

At-rest encryption, tenant-level authorization, OCR, reliable automated résumé fact classification beyond heading-based suggestions, historical revision restoration, and managed cloud hosting need separately scoped work.

## Previously approved packets after a change

When a profile or career-fact revision changes, previously drafted packets generated from the earlier workspace revision are flagged **outdated**. The dashboard no longer treats their old review approvals as current and refuses new approval or a manual-submission attestation against that obsolete packet. Regenerate a draft from the current verified facts and explicitly review it again. Previously recorded externally submitted packets remain preserved and locked.
