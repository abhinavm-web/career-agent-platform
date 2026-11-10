# Local development on Windows

Run these PowerShell commands from the repository root. Python 3.11+ and Node.js are required for the full test suite.

## First-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[graph,web,dev]"
```

The explicit executable paths avoid requiring virtual-environment activation or changing PowerShell execution policy. Re-run the install command after dependency changes.

## Run a fictional demo

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 ingest examples/jobs.sample.csv
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 serve --packet-dir .local/demo-packets --port 8765
```

Open http://127.0.0.1:8765/. Stop the foreground server with Ctrl+C. If the port is already occupied, use another port such as `8766` and open the matching URL. Restart after Python changes; refresh the browser after frontend changes.

The demo database and packets are gitignored and separate from the default personal workspace. Re-importing the sample CSV deduplicates the same postings. Use fictional data when experimenting in this demo.

To start an empty personal workspace instead:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli serve --port 8766
```

This uses `.local/career_agent.sqlite3` and `.local/packets`. Both demo and personal data are unencrypted local storage. The server binds only to loopback.

## Test

To enable **Find jobs with JobSpy** on the Opportunity board:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[jobspy]"
```

Restart the server after installation. Each search contacts external job sources only after you acknowledge the form's disclosure; tests below mock those requests.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check src/career_agent/web/app.js
node --test tests/test_dashboard_js.cjs
git diff --check
```

Tests use temporary databases and fictional fixtures. They do not submit applications or send recruiter messages.

## Contact editing smoke test

1. Open **Application tracker** and select a sample opportunity.
2. Add a fictional recruiter contact and a follow-up associated with that contact.
3. Choose **Edit contact**, change the name or email, then **Save contact**.
4. Confirm the corrected details appear and the linked follow-up uses the updated name.
5. Edit again, then **Cancel**; verify the saved details remain unchanged.
6. Refresh the page and reopen the opportunity to verify persistence.

See [CRM workflows](CRM.md) for pipeline and submission boundaries.

## Priority, filter and interview smoke test

1. Select a sample opportunity in **Application tracker**, choose **high** priority and save.
2. Combine a company/title search with stage and priority filters. Check the result count and try **Clear filters**.
3. Add fictional interview notes with a date and round/topic. Verify the note appears with its recording timestamp.
4. Refresh and reopen the opportunity. Confirm both the priority and notes persist, while submission status remains unchanged.
