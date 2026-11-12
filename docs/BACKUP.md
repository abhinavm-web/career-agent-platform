# Local workspace backup and restore

## Passphrase-protected backups

Install `python -m pip install -e ".[backup]"`, then add `--encrypted` to
both the backup and restore commands below. The command prompts for the
passphrase without echoing it; backup asks twice. Choose a strong passphrase
and keep it somewhere safe: it cannot be recovered if lost. Encrypted archives
use scrypt and AES-256-GCM, and restore verifies authentication before creating
the destination. Existing unencrypted ZIP backups remain supported without
`--encrypted`.

Encryption protects the final archive, not the working workspace. Backup and
restore temporarily stage plaintext files in a temporary directory
under the destination's parent; the operating system or disk may retain traces
after deletion. Use an encrypted local disk and a trusted account. The restore
destination itself is plaintext. Never commit archives or passphrases to Git.

## Backup procedure

Unencrypted backups contain private workspace data. Store them in a protected,
encrypted backup location and never commit them to Git. The backup copies the
main SQLite database, any sibling LangGraph workflow and discovery checkpoint
databases, and JSON files in the selected packet directory. SQLite's backup API
captures each database, including committed WAL data.

**Stop the dashboard and every other process writing to this workspace first.**
The databases and packet files are separate, so a live backup cannot guarantee
that all of them represent the same application state. The `--offline` flag is
your acknowledgement; the command cannot detect every possible writer. Keep
writers stopped until the operation completes.

In PowerShell, from the project directory (replace paths as needed):

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 backup private/workspace-backup.zip --packet-dir .local/demo-packets --offline
```

The destination directory must already exist; the archive must not. The chosen
packet directory must already exist, even when empty. The backup checks that
each job with a published workflow has a valid packet whose job ID and fingerprint
match its latest published workflow. It rejects missing, altered, oversized, or
older workflow packets rather than silently archiving inconsistent review state.
Published workflow packets are limited to 8 MiB for this validation.
When published workflows exist, their sibling workflow checkpoint database is
required as well; otherwise a restored review could not resume. Backup, verification
and restore also check for a root checkpoint entry for each published workflow,
without deserializing or running the graph. Missing entries or an unrecognized
checkpoint schema produce warnings while preserving the archive for salvage.
Presence does not prove checkpoint contents are valid, complete, current, or
resumable; test a restored workspace before relying on it.
If a legacy tool overwrote a workflow-managed packet, reconcile the packet or
resume the current workflow before backup. Other legacy/custom packets are copied
without interpreting their contents; their completeness cannot be established.
Backup, verification, and restore return a `warnings` list. If committed saved-search
imports exist but the discovery checkpoint database is missing, the backup remains
usable for salvage and warns that graph checkpoint history cannot be restored;
saved jobs, schedules, and import receipts remain in the main database. Unpublished
queued or failed workflows without a checkpoint database also produce a warning,
because retries may have to restart from stored inputs. An empty or never-run
workspace does not require either optional checkpoint database.
Check the path carefully. The archive is created atomically and an
existing destination is never overwritten. The
archive contains SHA-256 file checksums. Without `--encrypted` it is unencrypted. It excludes external
legacy `--profile`, `--facts`, and `--filings` files, AI route configuration by default,
credentials, and files elsewhere on your machine. Back up any such files
separately if you still use them. Do not put credentials in the archive.

### Optional AI route configuration

To include a specific route configuration, use an encrypted archive and opt in:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli --db .local/demo.sqlite3 backup private/workspace-backup.enc --packet-dir .local/demo-packets --offline --encrypted --ai-config .local/ai.json
```

The file is validated using the application's route parser, which rejects unsupported
credential fields, credentials in endpoint URLs, and symbolic-link paths. It stores
environment variable names such as `key_env`, never their values. Review the file
for secrets accidentally placed in other fields; route names, model identifiers and
endpoints may themselves be sensitive. Plaintext is temporarily staged as described
above. Restore validates the configuration into `ai-routes.json` in the new directory;
it never activates the file or overwrites your existing configuration. To opt into it
after reviewing it, set `$env:CAREER_AGENT_AI_CONFIG` to its absolute path before
starting the restored app. Set provider credentials separately in that shell.

## Verify a backup

Test an archive without replacing or changing your workspace:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli verify-backup private/workspace-backup.zip --encrypted --temp-dir private
```

Omit `--encrypted` for an unencrypted ZIP. Verification performs a temporary
restore, checking authentication for encrypted archives, paths, checksums,
SQLite integrity and published-workflow packet consistency. It removes the temporary directory afterward. `--temp-dir`
must name an existing directory on a trusted, encrypted disk; if omitted, the
operating system's temporary directory is used. Temporary files include
plaintext, and disk traces may remain. Verification does not prove that omitted
external files were backed up or that the captured data is complete or current.

To restore, keep the app stopped and choose a **new, non-existing** directory:

Saved searches retain their enabled or paused state. **Starting a restored
dashboard with enabled searches can immediately contact job boards.** Backup,
verification, and restore warn when enabled searches are present; those operations
themselves do not make discovery requests. Review and pause searches before backup
if you do not want them enabled in the restored copy.

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli restore private/workspace-backup.zip private/restored-workspace --offline
.\.venv\Scripts\python.exe -m career_agent.cli --db private/restored-workspace/workspace.sqlite3 serve --packet-dir private/restored-workspace/packets
```

Restore verifies the manifest, file checksums, safe paths, SQLite integrity and
published-workflow packet consistency.
It refuses an existing destination. If validation or extraction fails after
creating the new directory, partial files remain there for inspection; no
existing workspace is overwritten. Verify the restored workspace before making
it your primary copy, and restart with any original external configuration
files you still use. This is an offline portability tool, not automated backup,
at-rest encryption, multi-user hosting, or a guarantee against disk failure.

An optional Chromium integration test exercises a fictional application journey
through a stopped-server backup, verification, restore and browser restart. It
checks a reviewed packet, candidate-verified fact, recorded submission, tracker
entry and imported H-1B association. Job discovery and model responses are
mocked, and external browser requests are blocked; the test does not validate
live providers or the completeness of a particular user's backup.
