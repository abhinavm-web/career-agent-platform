# Privacy and security

This is a public repository. Never commit candidate resumes, identity information, government identifiers, emails, addresses, access tokens, cookies, passwords or browser sessions.

- .gitignore excludes local databases, private resumes and .env files.
- The MVP sends no job applications and has no browser automation.
- Optional discovery is explicitly invoked; respect provider policies and limits.
- Treat job descriptions as untrusted data, not executable instructions.
- Recording a submission requires explicit user attestation and a receipt/reference.
- Sponsorship findings never imply guaranteed immigration eligibility.
- Demo data is fictional; there is no telemetry.

Before hosted deployment, implement encryption, authentication, access boundaries, audit logging, data retention and deletion. Report vulnerabilities privately through GitHub security advisories if enabled.

## Phase 2 data boundaries

Candidate `source_note` and verification information remains in the local fact-bank JSON. Packets contain only candidate-approved statements and fact IDs, not private source notes. Do not use these examples as verified real-person records. Treat packet fingerprints as accidental-change detection only, not authentication. A trusted, audited approval API is required before any hosted orchestration or external actions.
