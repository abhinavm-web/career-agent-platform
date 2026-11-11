# Local workspace backup and restore

Backups contain private, unencrypted workspace data. Store them in a protected,
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
every published workflow job has a corresponding packet file. This catches
common wrong-directory mistakes, but cannot establish that a directory is
complete for legacy/custom packets or that matching files have the intended
version. Check the path carefully. The archive is created atomically and an
existing destination is never overwritten. The
archive contains SHA-256 file checksums and is unencrypted. It excludes external
legacy `--profile`, `--facts`, and `--filings` files, AI route configuration,
credentials, and files elsewhere on your machine. Back up any such files
separately if you still use them. Do not put credentials in the archive.

To restore, keep the app stopped and choose a **new, non-existing** directory:

```powershell
.\.venv\Scripts\python.exe -m career_agent.cli restore private/workspace-backup.zip private/restored-workspace --offline
.\.venv\Scripts\python.exe -m career_agent.cli --db private/restored-workspace/workspace.sqlite3 serve --packet-dir private/restored-workspace/packets
```

Restore verifies the manifest, file checksums, safe paths and SQLite integrity.
It refuses an existing destination. If validation or extraction fails after
creating the new directory, partial files remain there for inspection; no
existing workspace is overwritten. Verify the restored workspace before making
it your primary copy, and restart with any original external configuration
files you still use. This is an offline portability tool, not automated backup,
at-rest encryption, multi-user hosting, or a guarantee against disk failure.
