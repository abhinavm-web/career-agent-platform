# H-1B configuration and company research

Open **H-1B research** in the local dashboard, or select **Research H-1B records and jobs** on an opportunity. Set a new-sponsorship/transfer preference, earliest fiscal year for history counts, and whether to show jobs whose posting policy is unknown. These preferences are local search settings, not work-authorization answers or an eligibility determination.

The opportunity browser works immediately with saved job descriptions, without imported government records. Filter by role, employer, location, and approximate posting signal; search results are paginated and respect the saved new-sponsorship/transfer preference. Use **Find more jobs with JobSpy** to open discovery. Explicit positive, restrictive, unclear and absent wording are signals from the saved description, not verified employer commitments; check the original posting and confirm the specific H-1B path. The browser does not fetch jobs by itself.

Search uses a company-name substring against imported legal employer names and companies in stored jobs. Employer results are paginated in groups of 25. Job results include matching stored opportunities beyond the board's 500-job display limit. Changing the query clears earlier results. No automatic external search runs. Use the Opportunity board's explicit job discovery to add postings separately.

## Load public records

1. Visit the official [DOL OFLC performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance) page and download an LCA disclosure workbook.
   The command below accepts the original XLSX file directly, without a CSV conversion. Install workbook support first:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[lca]'
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 h1b-import .local/lca.xlsx --source-url "https://www.dol.gov/agencies/eta/foreign-labor/performance" --captured-on YYYY-MM-DD
```

Use the date you downloaded the file, not an assumed reporting cutoff. The file is parsed locally in read-only mode, and the entire import rolls back on invalid records. It remains labeled user-imported and not independently verified. The workbook must have exactly one sheet with the required LCA headers and be no larger than 512 MB; large workbooks can take time and memory, and Excel-compressed data is bounded. Worksite and appendix sheets do not substitute for the main disclosure data. Direct government download is not performed by this command.

For the existing dashboard import, or if you prefer CSV:
2. Export the disclosure sheet as **UTF-8 CSV**, preserving its DOL column headers. Do not use the worksite/appendix sheet as the main disclosure sheet.
3. In the import panel, supply the original DOL source URL and the date you captured that source, select the CSV and confirm provenance. The browser accepts files up to 8 MB (the encoded API request is limited to 10 MB). Larger CSVs can be streamed through the CLI:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 h1b-import .local/lca.csv --source-url "https://www.dol.gov/agencies/eta/foreign-labor/performance" --captured-on YYYY-MM-DD
```

Replace the date with the actual capture date and preferably cite the specific disclosure workbook URL. All storage remains in the selected local SQLite database. Imports are labeled **user_imported_not_independently_verified**: an official-domain URL is provenance supplied by you, not proof that the file was fetched or verified. The app makes no government requests and does not scrape third-party sponsorship sites. No API credentials are needed.

Required columns are `CASE_NUMBER`, `EMPLOYER_NAME`, `VISA_CLASS`, `CASE_STATUS`, `DECISION_DATE`. Optional displayed columns are `EMPLOYER_CITY`, `EMPLOYER_STATE`, `JOB_TITLE`. Other columns, including FEINs, attorney details and personal contacts, are not stored. ISO dates, ISO dates with midnight time, `MM/DD/YYYY`, and Excel date cells are supported. USCIS employer CSVs are not supported by this importer.

## Evidence and edge cases

- The [DOL record layout](https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Record_Layout_FY2025_Q3.pdf) describes the supported fields. Only `H-1B` rows enter this index. H-1B1 Chile/Singapore and E-3 Australian rows are counted separately as skipped; unrecognized visa classes reject the import.
- Certified, certified-withdrawn, denied and withdrawn cases have separate counts. Only currently stored **Certified** cases contribute to the recent-certified count. These counts represent distinct LCA case numbers, not workers, petitions, approvals or vacancies.
- Fiscal year is derived from decision date with the October 1 boundary. Source capture dates cannot be in the future or precede the decision. A configurable year window limits counts; the latest 20 records remain available for context even outside that window.
- Case numbers deduplicate overlapping releases. A later stored decision or capture date blocks an older replacement. Same-case, same-capture-date conflicting content rejects the entire import. Invalid rows roll back all writes from that import; no partial success is concealed. Updating a case retains its latest imported state, not an immutable archive of earlier source versions.
- Search suggestions are not entity matches. Automatic job-history joins use case/whitespace-normalized exact employer names only. Punctuation, suffix differences, brands, subsidiaries, acquisitions and staffing clients are not silently merged. Even an exact name can be ambiguous; inspect the legal entity, city/state and original source. For a saved job, you may associate an exact name already in the imported records, recording how you checked it and acknowledging that it is unverified. The association applies only to that job, can be removed, and retains a local revision history. It does not establish entity identity, current hiring, sponsorship or eligibility; the posting assessment remains independent.
- Coverage is always labeled incomplete. Empty results, old fiscal years, an empty dataset or missing matches mean **unknown**, never proof of no sponsorship. Source dates are visible; recent capture does not imply a current role-level commitment.
- Posting rules preserve matching text and distinguish `excluded`, `conflicting`, `conditional`, `transfer_only`, `transfer_supported`, `possible` and `unknown`. Explicit exclusions override history. Conflicting support/restrictions require clarification. Conditional language and screening questions do not become positive claims. Generic sponsorship wording requires H-1B category confirmation. Transfer support alone leaves new sponsorship unspecified; only explicit exclusivity is called transfer-only. Explicit new-sponsorship support is preserved when transfers are also supported. Unrelated restrictions such as remote-work availability do not negate sponsorship.
- Posting interpretation is a deterministic English-language heuristic, not complete natural-language understanding. All stored job links require a fresh check: this feature does not verify that the vacancy remains open, that sponsorship applies to the location, or that an individual qualifies for transfer, cap exemption or a new petition. No legal screening answers are inferred.

The existing packet evidence files remain a separate, manually curated input. Importing LCA records does not modify saved packets, approvals or candidate facts. [USCIS refresh/import and live ATS checks](USCIS_LIVE_CHECKS.md) are available as separate, explicit actions. DOL workbook downloads, recruiter-confirmed policy records and checks for other job sites remain future work.

## Validation

Unit/API tests cover entity boundaries, case deduplication, atomic rollback, stale decisions, unsupported visa/status rows, fiscal-year boundaries, authentication, source dates, literal searches, pagination and posting-language edge cases. The optional real-browser test imports a fictional CSV, saves preferences, searches, checks transfer-only behavior and verifies that untrusted record text cannot execute HTML. Enable it with `CAREER_AGENT_TEST_BROWSER=1` after installing Playwright Chromium. CI runs it alongside the existing suite.
