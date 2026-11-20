# Privacy and security

This is a public repository. Never commit candidate resumes, identity information, government identifiers, emails, addresses, access tokens, cookies, passwords or browser sessions.

- .gitignore excludes local databases, private resumes and .env files.
- The browser companion can activate a recognized Lever submission control following an explicit, single-use review in the active tab. See [the application workflow](docs/BROWSER_COMPANION.md#apply-with-lever).
- Optional discovery is explicitly invoked; respect provider policies and limits.
- Treat job descriptions as untrusted data, not executable instructions.
- Recording a submission requires explicit user attestation and a receipt/reference.
- Sponsorship findings never imply guaranteed immigration eligibility.
- Demo data is fictional; there is no telemetry.

Before hosted deployment, implement encryption, authentication, access boundaries, audit logging, data retention and deletion. Report vulnerabilities privately through GitHub security advisories if enabled.

## Phase 2 data boundaries

Candidate `source_note` and verification information remains in the local fact-bank JSON. Packets contain only candidate-approved statements and fact IDs, not private source notes. Do not use these examples as verified real-person records. Treat packet fingerprints as accidental-change detection only, not authentication. A trusted, audited approval API is required before any hosted orchestration or external actions.


## Local packet file paths

Packet saves reject symlinks in the destination filename and existing parent directories.
On platforms with O_NOFOLLOW, the final file descriptor also refuses a symlink.
These checks protect against accidental redirected local paths; they are **not a
complete defense against an attacker concurrently replacing parent directories**.
Before multi-user hosting, replace local path-based writes with an isolated
private storage service or descriptor-relative, race-resistant file operations.


## Local web workspace threat model

The dashboard is accessible only at 127.0.0.1, with a random per-process X-Session-Token required for all API calls, restrictive Host and Origin checks, no enabled CORS, JSON-only writes, strict body-size limits, a no-store policy, and a restrictive Content Security Policy. No third-party JavaScript is loaded.

The above mitigations reduce accidental cross-site and DNS-rebinding exposure; they do **not** provide production-ready authentication. A local process running under the same user account may still access unencrypted files or potentially use the loopback interface. Do not expose ports publicly, use reverse proxies to share the service, or store highly sensitive files on a shared machine. Imported job text is treated as untrusted and inserted into the UI using safe DOM text nodes, never HTML execution.

Review decisions are appended to local SQLite. Draft regeneration invalidates previous approval for the new fingerprint. Users must attest to an external employer confirmation. See docs/DASHBOARD.md.


## R1 editable career facts

Profiles, fact statements, verification states, private evidence notes and earlier revisions are stored in **plaintext SQLite** on the local computer. They are not committed to Git. Only current candidate-verified facts are used in new application packets; changing a fact's content revokes prior verification. Revision history deliberately retains past notes and claims. No identity security, tenant isolation, database encryption, backup guarantee, or secure deletion is provided by this preview. Do not use it as a hosted or shared-user service. See docs/CAREER_WORKSPACE.md.

A workspace-generated application packet is bound to its source workspace revision. Subsequent changes or fact revocations invalidate the prior packet for new approval/submission actions. Approved but unsubmitted old drafts must be regenerated and re-reviewed; already submitted records and packets remain preserved.


## CRM contact and follow-up data

Recruiter names, emails, private notes, interview/outcome status and due-date tasks are stored **only in local plaintext SQLite**. No CRM action sends external messages, contacts an employer, runs background checks or updates external calendars. The applied state is derived only from a separately attested application submission. Do not expose the local dashboard publicly or run it on a shared/untrusted computer. Hosted use needs encryption, authentication, deletion policies, audit protection and an integration-specific security review.


## Repository privacy check

Run `python scripts/check_repository_privacy.py` after staging changes. CI also
runs it. It inspects Git's indexed bytes and rejects private data paths, source
workbooks, document/database files and known credential forms. Reports contain
file locations and categories, never matched values. Keep real-data tests and
exports under ignored `.local/`; commit only fictional fixtures.

This guard does not detect every secret or establish that all history, commit
attribution, pull-request text, caches or remote copies are clean. Review those
surfaces separately when auditing an existing public repository. Use a GitHub
noreply address for future Git attribution when personal email privacy matters.


## Browser application and account boundaries

The optional companion operates in the current browser profile. It grants temporary access to the active tab and restricts application assistance to a recognized HTTPS Lever host, posting path, form, and visible submit control. The popup displays candidate answers and selected filenames. The final approval binds the tab, document, posting, form state and selected File objects. The provider owns its page scripts, validation, uploads and response. A requested click is insufficient evidence of receipt. Verify the selected document on the employer’s page before approving.

Employer sign-up and sign-in use the browser’s password manager. The companion opens the manager’s settings. The browser profile and its sync policy govern credential storage, access, and deletion. Passwords and session cookies are outside the dashboard, model prompts and application backups.
