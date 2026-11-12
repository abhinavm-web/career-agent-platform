# H-1B employer evidence methodology

## Sources

Authoritative original data should come from the [U.S. Department of Labor Office of Foreign Labor Certification performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance), specifically LCA program disclosure files and their field layouts. Employer name, fiscal year, and official source URL should remain attached to every imported record.

The legacy packet/CLI evidence adapter accepts **manually curated CSV records** in the format below. The dashboard provides an [H-1B research workspace](H1B_RESEARCH.md) for importing DOL LCA CSV files, company searches and job-policy assessments; the separate CLI importer also accepts XLSX workbooks with optional dependencies. DOL imports do not download or independently authenticate government records. A separate [USCIS adapter](USCIS_LIVE_CHECKS.md) can refresh supported annual CSV files directly or import them locally.

Format:

    employer,fiscal_year,source_url,source_type,case_status
    Example Industries,2025,https://www.dol.gov/agencies/eta/foreign-labor/performance,example-only,

**That row is fictional, not evidence of a real filing.** Replace it with real, inspected records before relying on the history.

## Interpretation

Sponsorship statuses:

- \`explicit_exclusion\`: keywords in the posting appear to exclude visa sponsorship. Confirm role-specific language with the employer.
- \`posting_claims_sponsorship\`: keywords in the posting appear to permit sponsorship. Still requires role-specific confirmation.
- \`historical_only\`: source-backed, user-provided employer records match the exact company name, but the current posting provides no clear sponsorship policy.
- \`unknown\`: no recognized job-specific statement and no matching historical record.
- `conflicting`: posting contains both support and restriction language; do not resolve the conflict using company history.
- `conditional`: wording is conditional, a screening question, or unclear; no commitment established.

A DOL LCA is an employer labor condition filing, not an approval of a worker's H-1B petition, a commitment to sponsor a new employee, or legal advice. A company can sponsor some roles and not others. Name matching is deliberately exact after case/whitespace normalization. Subsidiaries, alternate legal names, and acquired companies require separate human research.

Never automatically answer screening questions about citizenship, status, petitions, or future sponsorship. Consult qualified immigration counsel about individual circumstances.

## Future scope

- Download official workbooks directly and independently validate reporting-period metadata; support additional USCIS release formats.
- Improve reviewed employer legal-entity mapping beyond explicit candidate-provided associations.
- Add current employer career-page statements and explicit timestamps.
- Keep employer historical probability models separate from job-specific assertions.
