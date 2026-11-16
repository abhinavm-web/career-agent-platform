# Experimental PostgreSQL development service

This service prepares an isolated PostgreSQL target for development and testing.
**The application does not yet use PostgreSQL.** Starting it does not move or
synchronize jobs, profile information, H-1B records, packets or checkpoints.
Keep using the existing SQLite workspace until a tested migration is available.

From PowerShell, choose a strong local development password and set it in the
current shell without committing it to the repository. For example, prompt
interactively rather than writing the value to a command or file:

```powershell
$credential = Get-Credential -UserName career -Message 'Choose a PostgreSQL development password'
$env:CAREER_POSTGRES_PASSWORD = $credential.GetNetworkCredential().Password
docker compose -f compose.postgres.yaml up -d
```

PostgreSQL listens only on `127.0.0.1:54329` by default. Override the port with
`CAREER_POSTGRES_PORT` if needed. `docker compose -f compose.postgres.yaml stop`
stops it and leaves its volume intact. `down -v` removes the volume and its
data. The password initializes a new database volume only; changing the
variable later does not change an existing database user's password.

A hosted PostgreSQL provider could eventually replace the local database service,
without copying credentials into Git. Do not put real profile data in a hosted
database until the application supports PostgreSQL, migration, access control,
backups and the associated privacy review. The local dashboard remains bound to
loopback and is not a production multi-user deployment.

## Remaining integration

The workspace store, SQLite-specific SQL and transaction semantics, both
LangGraph checkpointers, packet storage, backup/restore, scheduler locking and
all data migrations must be adapted and tested together. In particular, moving
only current table rows would omit workflow history and can break approval and
recovery guarantees. Migration should use a stopped-server snapshot, preserve
the SQLite source, and verify row counts and representative application flows.
