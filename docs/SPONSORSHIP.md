# H-1B employer evidence methodology

## Sources

Authoritative original data should come from the [U.S. Department of Labor Office of Foreign Labor Certification performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance), specifically LCA program disclosure files and their field layouts. Employer name, fiscal year, and official source URL should remain attached to every imported record.

The current MVP accepts **manually curated CSV records**, not the original DOL .xlsx workbooks. It does not download or automatically authenticate the documents. URL presence is validated syntactically only; each CSV entry is marked \`user_supplied_not_independently_verified\`.

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

A DOL LCA is an employer labor condition filing, not an approval of a worker's H-1B petition, a commitment to sponsor a new employee, or legal advice. A company can sponsor some roles and not others. Name matching is deliberately exact after case/whitespace normalization. Subsidiaries, alternate legal names, and acquired companies require separate human research.

Never automatically answer screening questions about citizenship, status, petitions, or future sponsorship. Consult qualified immigration counsel about individual circumstances.

## Future scope

- Parse official DOL data with verified quarterly provenance, case-status filters and fiscal years.
- Cross-reference employer legal entities with manually reviewed mappings.
- Add current employer career-page statements and explicit timestamps.
- Keep employer historical probability models separate from job-specific assertions.
