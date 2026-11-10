# Open-source license and dependency assessment

Inspected November 2026. JobSpy is an optional dependency. The résumé PDF feature adapts selected Career Ops template CSS and Chromium print settings; other product code remains original unless marked otherwise.

| Upstream | Purpose | License |
| --- | --- | --- |
| https://github.com/speedyapply/JobSpy | Optional US job-discovery Python dependency | MIT, copyright 2023 Cullen Watson |
| https://github.com/career-ops-hq/career-ops | Adapted résumé styling and PDF print settings; reference for other workflows | MIT, copyright 2026 Santiago Fernández de Valderrama |
| https://github.com/vaibhavarora14/job-application-agent | Reference for human-reviewed application operations | MIT, copyright 2026 as stated upstream |

MIT notices must be preserved when source code is copied or substantially bundled. Review transitive licenses separately. JobSpy is opt-in and not vendored. JobAppAgent is not imported or run.

## Career Ops résumé PDF adaptation

- Upstream commit: `605a1e61ee2b0d269b371ebe4351180993ebbfee`.
- Sources: `templates/resume-template.html` and the Chromium print settings in `generate-pdf.mjs`.
- Local adaptations: `src/career_agent/templates/resume.css` and `src/career_agent/resume_pdf.py`.
- Full MIT notice: `src/career_agent/third_party/CareerOps-LICENSE.txt`, included in the Python package.
- Changes: system fonts instead of bundled font assets; US Letter paper; escaped saved packet text instead of upstream profile placeholders; no photos, external resources, tracker hooks or upstream workspace dependencies. The small Python renderer uses optional Playwright, with JavaScript disabled and network requests blocked.

This imports selected styling and rendering settings, not the complete Career Ops document pipeline or its factual-audit system. Our existing packet verification and version-bound approval remain the export gate.

Before ATS automation, verify terms of service, browser security, user consent and privacy defaults.
**Original project license:** pending repository-owner choice. A public repository is not automatically open-source licensed.
