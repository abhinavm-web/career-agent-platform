# Automatic discovery and durable LangGraph workflows

## Automatic JobSpy searches

Install `pip install -e ".[web,jobspy]"`, then run the local server normally. Open **Saved searches** in the navigation, enter a query/location, choose job boards and frequency, acknowledge recurring requests, and enable the search. No CSV is needed. The first run becomes due immediately; the worker checks every 15 seconds. New jobs appear on the opportunity board after Refresh.

JobSpy search results can omit descriptions, notably for LinkedIn. Manual and saved searches offer an optional **Request individual job details** setting. It opts in to additional external per-job requests where JobSpy supports them, is limited to 20 requested results per source, and may still return incomplete descriptions or time out. Sources may impose restrictions; check their terms. The setting is off by default, including for existing schedules, and requires JobSpy 1.4.1 or later. Enabling it never overwrites existing saved jobs: for a previously saved job with no description, review its original posting and paste the text into **Correct saved description**. This is a manual local correction with its existing revision and draft-review safeguards.

Up to five saved searches are supported, with 1–50 results requested per source, intervals of 1 hour to 7 days (default 6 hours), and a shared one-minute cooldown between searches. Pause/resume settings survive restarts. Pause a search before removing it; run history remains. Search filters are sent to selected sources, not candidate facts, notes or API keys. Use only sources whose terms permit your intended use; this feature adds no proxy rotation, login automation or access-control bypass.

To change the query, location, sources, result count or frequency, pause the search and open **Edit paused search**. Confirm the revised settings and save; the search remains paused and its existing run history is retained. Run history does not snapshot the former query or all previous settings. Resume separately to permit future network requests. If a paused run still holds its active lease, wait for it to finish before editing or resuming; this prevents old results from being imported after settings change. A stale editor must be reloaded. Resuming an overdue search may contact the selected sources immediately.

For a paused search with multiple sources, **Create separate paused source searches** makes one paused copy per source, leaving the original paused and retaining its history. This lets each resumed source have independent future backoff, so one failing source need not slow a successful one. Copies inherit the original next due time and failure count, so splitting does not bypass existing backoff. Resume each copy separately to permit requests; avoid also resuming the original unless duplicate searches are intended. The shared cooldown still applies. The operation creates all copies or none and is unavailable if a matching single-source search exists or the five-search limit would be exceeded.

The worker runs for the lifetime of the server, even with no browser open. It does **not** run while the computer/server is stopped. On restart, overdue searches run once rather than replaying every missed interval. A crashed worker's five-minute database lease must expire before another worker can recover its unfinished schedule. It records the interrupted attempt and performs a fresh discovery attempt; network requests are not exactly-once. A paused search may finish its current network read, but its results are discarded before import. Normal shutdown kills an active child within a few seconds.

Each run uses a persisted LangGraph: **discover → normalize/deduplicate → import**. JobSpy runs in an isolated child process with a two-minute deadline. The parent can kill a stalled scraper. Imports validate the schedule and lease, and commit jobs plus an idempotency receipt in one transaction. URL-based duplicate detection preserves existing job descriptions and reviewed applications; it does not automatically merge different URLs for the same role or establish posting freshness.

For a saved opportunity, **Find possible duplicates** compares company, title and nonempty location using case and whitespace normalization, across up to 10,000 recent saved opportunities. It shows up to 10 matches, their original links, source labels and whether an application submission was recorded. Matching fields do not prove two postings share a requisition. Different names, locations or titles, missing locations, and records outside the scan can still be duplicates. The lookup never merges, removes or changes records, reviews or approvals.

Successful manual and scheduled JobSpy imports record the first and latest time each reported source returned a URL, plus the number of searches that returned it. Duplicate rows from the same source in one search count once; other sources remain separate. Inspect this history in the opportunity details. An observed return is not an independent live availability check, and is not evidence that the posting is current or unchanged. Earlier imports and manually added opportunities have no retroactive discovery history. Empty, failed, stopped, or discarded runs do not refresh the history.

For new discovery observations, the app compares a fingerprint of the exact latest returned description with the current saved description. Any difference, including formatting or capitalization, is a prompt to inspect the original posting, not proof that a job changed: source text may be incomplete, stale or different from your correction. Empty source descriptions and older, case-and-whitespace-insensitive fingerprints remain unknown until the source returns the job again. Saved descriptions, drafts and approvals are never overwritten or automatically changed by this comparison; make a reviewed description correction when appropriate.

Run history shows returned/new counts, timestamps and completed/partial/failed/inconclusive/interrupted states. Per-source outcomes show whether JobSpy reported an error or warning, returned usable rows, or returned no usable rows. These indicators omit upstream error details and can miss errors that a provider does not report. Empty JobSpy results are inconclusive: some source adapters return empty data on errors. Positive results may also be incomplete. Failed, partial or empty runs back off (at least the configured interval, with exponential backoff up to one day). No missing result is interpreted as a closed vacancy. Detailed upstream exception text is not retained.

JobSpy can log skipped ZipRecruiter rows without returning structured warnings.
The worker marks that source as having reported a warning when it observes this
specific condition, even if other rows were returned. It retains no provider
message or affected posting details. This detection is best-effort and does not
repair upstream parsing or establish source completeness.

## Durable application preparation

The opportunity board's **Create application draft** now runs:

**Analyze fit → assess sponsorship → draft from verified facts → validate → interrupt for human review.**

This is an executing LangGraph with typed state and synchronous SQLite checkpoints, not an architecture sketch. The dashboard and CLI `workflow` command persist run IDs, input snapshots/hashes, packets and review state. An app restart does not discard pending review. Model-backed requirement analysis remains separately opt-in; this graph uses deterministic evidence-based matching and verbatim candidate statements, so it runs without a paid provider and does not silently transmit a profile.

The workflow panel lists recent runs. Use **Resume local workflow** after a failed/queued run; completed graph steps are loaded from checkpoints. Retries require unchanged inputs. A newer draft supersedes old runs. The review fingerprint and all input snapshots—including legacy JSON profile/fact inputs—are checked before approval. A deliberately changed decision runs through another human-review interrupt; repeating the same decision does not duplicate the audit record.

Graph nodes perform local computations. Packet publication is atomic, and approval audit persistence is idempotent. SQLite serializes application advancement across local server processes. Failures after checkpointing can be reconciled by retry; the graph never records a submission or sends an application. Existing manual submission attestation and PDF approval gates remain in force.

CLI usage:

```text
career-agent --db .local/career.sqlite3 workflow JOB_ID --profile .local/profile.json --facts .local/facts.json
career-agent --db .local/career.sqlite3 workflow JOB_ID --profile .local/profile.json --facts .local/facts.json --resume-run RUN_ID
```

Enter leaves review pending. Type the exact displayed APPROVE/REJECT phrase to record a decision. The older standalone `review PACKET` command remains an in-memory interactive utility; use `workflow --resume-run` for durable CLI runs.

## Storage and operational limits

- Main database: saved searches, discovery history/import receipts and workflow registry. Sibling `.workflows.sqlite3` and `.discovery.sqlite3` files: LangGraph checkpoints. Preserve these files and packet storage together when making a stopped-server backup.
- Private checkpoints include profile/fact snapshots and are **unencrypted**, like the existing local database. They are not included in tracker-history exports; retention/deletion controls remain future work.
- External LangSmith tracing is explicitly disabled for these graph executions. No remote agent tracing or telemetry is added.
- This is a single-user local implementation, not a distributed queue or authenticated hosted service. SQLite, bounded schedules and one active discovery lease constrain concurrency. A child has its own 125-second exit watchdog in case the parent is force-killed; abandoned results cannot import without the current lease.
- Optional, consented model-assisted wording and fact suggestions are available separately; see [AI models](AI_MODELS.md). The broader PRD's follow-up/interview agents, external ATS submission, scheduled AI calls and hosted orchestration are not implemented. No application transmission is authorized or performed.
- Tests exercise real LangGraph/SQLite and Chromium with fictional data and mocked JobSpy responses, including failure/restart/approval paths. Live job-board completeness and provider availability are not established by those tests.

Architecture uses the official [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts) interfaces and the optional [JobSpy package](https://github.com/speedyapply/JobSpy). No upstream application source was copied in this increment.
