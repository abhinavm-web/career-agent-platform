# USCIS records and live job observations

The H-1B research tab separates three sources: DOL LCA history, USCIS employer decision aggregates, and dated public ATS posting checks. None determines individual immigration eligibility or guarantees an offer of sponsorship.

## USCIS refresh and import

Open **USCIS employer decisions: refresh or import**. Find the official annual CSV on the [USCIS files page](https://www.uscis.gov/archive/h-1b-employer-data-hub-files). Supply its file URL, fiscal year and reporting cut-off date, then confirm that it is a complete fiscal-year file, not a filtered company export. **Refresh from USCIS** downloads the selected file over HTTPS. If access is blocked, download the original file yourself and use **Import official CSV locally**. The browser accepts files up to 8 MB; refresh accepts up to 32 MB. A larger local import within that 32 MB limit can use:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 uscis-import .local/uscis.csv --source-url "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-2023.csv" --fiscal-year 2023 --data-as-of 2023-09-30
```

The command illustrates the annual filename convention. Verify the actual source and cut-off date before use; it does not assert that this URL is reachable or the newest available release. The app does not automatically discover the newest USCIS file or scrape a per-company search endpoint.

Supported source URLs are HTTPS `uscis.gov`/`www.uscis.gov` paths under `/sites/default/files/`, named `h1b_datahubexport-YYYY.csv`, with no credentials, queries or fragments. Other visa programs and release formats are rejected rather than guessed. Required CSV headers are `Fiscal Year`, `Employer`, `Initial Approval`, `Initial Denial`, `Continuing Approval`, `Continuing Denial`, `NAICS`, `Tax ID`, `State`, `City`, `ZIP`. UTF-8 with an optional BOM is supported. Full tax identifiers are rejected; only the public short suffix is accepted. Blank identity dimensions stay blank, and employer rows without names cannot match a named company.

Each year has one active snapshot. A later reporting period replaces that year's entire snapshot atomically; cumulative quarterly releases are never added together. Other fiscal years stay intact. Older periods, conflicting content for the same date, malformed rows and conflicting duplicate groups reject the update. Exact duplicate groups are counted once. Missing/suppressed counts remain unknown, including in totals. A failed or blocked request does not remove previous data.

The snapshot records source URL, retrieval time, raw-file SHA-256, normalized-content SHA-256 and transport provenance. **downloaded_from_uscis_https** means bytes came from that official HTTPS endpoint; **user_imported_not_independently_verified** means a local file was supplied. The reporting date is always explicitly labeled **user-supplied** because these CSV rows do not independently establish the release cut-off. A successful fetch does not prove the source's completeness, latest status or substantive accuracy. No real USCIS snapshot is seeded into the repository.

USCIS initial/continuing approval and denial counts are shown separately from LCAs, with year, location and source. They describe historical first decisions, not new-cap versus transfer eligibility. The original USCIS explanation is available in an [archived copy of Understanding Our H-1B Employer Data Hub](https://www.aila.org/aila-files/1D5ACD3D-64CF-407C-867A-A3893F6B3E25/19040173.pdf). Company joins remain exact after case/whitespace normalization; no subsidiary or legal-entity inference is added.

## Live ATS checks

On a research result, select **Check live ATS posting**. The action sends only a public posting identifier to the provider. Supported URLs are Greenhouse `/board/jobs/id` URLs on `boards.greenhouse.io` and `job-boards.greenhouse.io`, and Lever `/board/uuid` URLs on `jobs.lever.co` and `jobs.eu.lever.co`. Custom employer domains, aggregators, JavaScript-rendered pages, other ATS products and application-form submissions are not supported.

The adapters use the providers' documented read-only APIs: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) and [Lever Postings API](https://github.com/lever/postings-api). They require the expected posting ID and matching title. A different Greenhouse company name requires manual entity review. Greenhouse prospect posts are labeled general-interest. A valid provider deadline in the past is labeled expired; a malformed or timezone-free deadline stays unknown.

**listed** means the exact posting was returned at check time, not that an employer is actively hiring or will sponsor. HTTP 200 alone is insufficient. Redirects, 403/404/429 responses, timeouts, HTML challenge pages, malformed responses and identity mismatches stay **unknown**. A 404 is not automatically called filled or closed.

Each check is appended to local history with timestamp, source, HTTP status, reason and (when usable) a content hash and quoted policy evidence. Checks older than 24 hours have effective availability **unknown**, retaining the original observation for context. A failed newer check does not silently reuse a prior positive result. Policy wording from the live response is displayed separately from the saved posting. Saved job descriptions, candidate facts, approval fingerprints and application packets are not modified.

## Network boundaries and validation

Requests use fixed provider allowlists, verified TLS, public-only DNS addresses pinned for the connection, no redirects or environment proxies, no cookies/credentials/session tokens, bounded response sizes and read timeouts. Sources may still block or rate-limit access; the app reports the failure instead of bypassing it. Company searches themselves remain local.

Offline tests cover snapshot replacement, malformed counts, suppressed values, overlapping releases, stale observations, wrong identities, unrelated hosts, private DNS, redirects, errors, deadlines and prospect posts. The real-browser test imports fictional USCIS data, exercises a simulated 403 without data loss, searches and checks an unsupported posting. During implementation, live Greenhouse and Lever reads succeeded; USCIS returned HTTP 403. Therefore USCIS parsing is fixture-tested, but a successful live USCIS refresh has not been validated in this environment.
