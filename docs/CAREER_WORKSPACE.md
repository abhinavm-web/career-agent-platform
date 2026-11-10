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

New or modified facts are *not verified*. A separate explicit acknowledgement is necessary to mark each statement verified. **Only verified facts can enter generated résumés or cover notes.** Modifying a statement, category, keywords, or private evidence note revokes that verification until you acknowledge the revised statement again.

The current source note is only returned inside the local authenticated workspace. It is never inserted into the outward-facing draft.

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

At-rest encryption, tenant-level authorization, secure document uploads, machine-extracted résumé fact confirmation, historical revision restoration, full application CRM, and managed cloud hosting. These need separately scoped, reviewed work.
