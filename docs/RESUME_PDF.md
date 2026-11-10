# Reviewed résumé PDF export

The Opportunity board can download a résumé PDF from the exact saved packet you approved. It adapts Career Ops résumé styling and Chromium print settings; attribution and the upstream commit are recorded in [UPSTREAM.md](UPSTREAM.md).

## Setup

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web,pdf]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Restart the local server. Installation downloads the renderer/browser; PDF generation itself blocks network requests and uses system fonts.

## Use

1. Save and verify your career facts.
2. Create a packet for an opportunity and review every statement.
3. Approve the current packet for manual submission.
4. Select **Download reviewed resume PDF**, then inspect the downloaded document.

Exports use US Letter pages and contain the saved résumé text only. Private source notes, CRM records, cover notes, sponsorship analysis and internal review questions are excluded. The filename includes the job ID and packet fingerprint prefix. Downloading does not submit an application or change its status. The server renders bytes in memory; the browser saves the file to your chosen download location.

The API requires the session token, exact current packet fingerprint and an approval of that packet. Missing, rejected or outdated packets cannot be exported. Changes to the career workspace require a fresh draft and review. Submitted packets remain immutable; if later profile changes make one stale, this endpoint does not export it as current.

## Limits and verification

Only the deterministic packet format is supported: headings, plain paragraphs and bullets. Raw HTML is escaped; arbitrary Markdown links, images and styling are not interpreted. Contact details are not synthesized. No guaranteed ATS acceptance, page-count limit, DOCX export, cover-letter PDF or historical-export workflow is provided. Unicode coverage depends on installed system fonts; inspect names and non-Latin text before use.

Optional real-render tests (also run in CI):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[pdf,pdf-test]"
$env:CAREER_AGENT_TEST_PDF = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests verify selectable text, every fact in a multi-page fixture, private-field exclusion, HTML escaping, stale/rejected packet guards and missing-renderer errors. Visual QA uses fictional fixtures only.
