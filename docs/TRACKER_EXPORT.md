# Tracker history export

In **Application tracker**, select **Download tracker history** to save `career-tracker-history.json`. The download includes all stored opportunities and tracker records, regardless of active filters or display limits. No external service is contacted and no application status changes.

The file contains private contact details, notes and submission references. It is unencrypted; keep it outside public repositories and shared folders. The download excludes career preferences, verified facts, their private evidence notes, workspace revisions and application document files. This is a tracker export, not a complete workspace backup or a restorable database. Import/restore is not implemented.

## Format version 2

The top-level `format` is `career-agent-tracker-history`, with `schema_version: 2`, an ISO UTC `exported_at` timestamp and a `records` object. Version 2 adds `stage_corrections`; all version 1 collections retain their fields. Collections are normalized and joined by their stored IDs:

| Collection | Contents |
| --- | --- |
| `jobs` | All stored postings, source URLs, descriptions and job IDs |
| `applications` | Current review/submission stage, candidate-entered receipt reference and last update |
| `reviews` | All recorded packet decisions, fingerprints and decision dates |
| `stage_events` | All recorded organizer and post-submission pipeline changes |
| `stage_corrections` | Links each corrected terminal event to its appended correction event |
| `priority_events` | All recorded priority changes |
| `contacts` | Current recruiter/contact records, including private notes |
| `interview_notes` | All dated interview notes |
| `followups` | Tasks, linked job/contact IDs, current due dates and completion times |
| `followup_events` | Creation, rescheduling, completion and cancellation events linked to tasks |

A cancellation is represented by a `cancelled` event; its task has no completion time. Events are ordered by their numeric IDs, and jobs/applications by job ID. SQLite event timestamps are UTC; due dates and interview dates are candidate-entered calendar dates without a time zone. `remote` is stored as `0` or `1`. Empty collections are empty arrays.

The export uses one database read transaction so concurrent edits cannot mix different snapshots. It reads explicit columns and has no dashboard row caps. Contact edits retain only the current record because the CRM does not store contact edit history. Application records likewise represent the current stored state; the export does not invent missing historical events or certify employer receipts.

`GET /api/crm/export` requires the existing local session token and Host/Origin protections. Its response is a JSON attachment with `Cache-Control: no-store`. The complete file is assembled in memory, intended for a single-user local workspace; large databases may take longer to export.
