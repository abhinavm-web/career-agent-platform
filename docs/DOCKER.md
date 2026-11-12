# Run locally with Docker

The Compose setup runs the existing single-user dashboard and stores its SQLite
database, LangGraph checkpoints, AI route configuration and generated packets in
the `career-data` Docker volume. The volume is separate from `.local` on the host;
starting this container creates a new, empty workspace and does not migrate or
modify an existing local workspace.

1. Install Docker Engine on Linux, or Docker Desktop 4.34 or later with Linux
   containers. On Docker Desktop, enable **Settings → Resources → Network →
   Enable host networking**, apply and restart. See [Docker's host networking
   instructions](https://docs.docker.com/engine/network/drivers/host/).
2. Choose an unused port. If the existing local server already uses 8765, either
   stop it or set `CAREER_AGENT_PORT` in the terminal that runs Compose. For
   example, in PowerShell: `$env:CAREER_AGENT_PORT = '8766'`.
3. Run `docker compose up --build -d` from the repository root. Open
   `http://127.0.0.1:8765/`, or the chosen port, on the same computer.
4. Use `docker compose logs -f` to inspect startup and `docker compose down` to
   stop. The volume remains across container rebuilds and `down`; **`docker
   compose down -v` deletes the volume and its private workspace data**.

Host networking preserves the application's loopback-only server and allows
loopback Ollama/LM Studio endpoints. It is required here; ordinary Docker port
publishing cannot reach a server bound only to the container's loopback address.
Do not expose the app to a network: it is not authenticated multi-user software.
Docker Desktop's host-network feature must be enabled; if it is not, the app may
start inside Docker but the host browser and host model servers will be unable to
reach it. Port conflicts still apply. The app requires a trusted local computer;
Docker administrators and local users with Docker access can read its data.

## AI configuration

Create a route configuration on the host as described in [AI models](AI_MODELS.md),
then copy it to the running container's persistent volume:

    docker compose cp .local/ai.json career-agent:/app/.local/ai.json

Repeat the copy after editing the host file; the two copies are independent. The
dashboard loads this configuration; it does not currently edit routes. The container passes through
`ANTHROPIC_API_KEY` and `AZURE_OPENAI_API_KEY` only if set in the shell running
Compose; never put a key in the Dockerfile, image, route configuration, or Git.
Recreate the container after changing a key in the shell. Docker exposes container
environment values to users with Docker access. Local models must be running on
the host and use the existing literal-loopback `/v1` endpoint requirement.
Other credentials or custom `key_env` variables require an explicit Compose
override. The image includes JobSpy, the AI providers, PDF resume extraction and export,
and encrypted backup dependencies. The bundled Chromium renderer increases image size.

## Storage and backups

SQLite is already the durable store; PostgreSQL is not supported by the current
application or its SQLite-backed LangGraph checkpoints. Adding a PostgreSQL
container without changing those integrations would not migrate the data. A future
multi-user deployment would need a deliberate database and authentication design.
The volume is plaintext and may contain private candidate information. Stop the
service before taking an application backup so the databases and packets are
captured consistently. These commands create an encrypted archive **inside** the
volume, then copy it to an existing protected host directory (choose your own
paths and a strong passphrase):

```powershell
docker compose stop career-agent
docker compose run --rm --no-deps -i career-agent --db .local/career_agent.sqlite3 backup .local/workspace-backup.enc --packet-dir .local/packets --offline --encrypted
docker compose start career-agent
docker compose cp career-agent:/app/.local/workspace-backup.enc private/workspace-backup.enc
```

The packet directory must exist, the archive must not already exist, and no other
workspace writers may run. Keep the copied archive on protected storage, apart
from this volume. Verify and restore following [Local workspace backup](BACKUP.md);
remember the restored directory and temporary staging contain plaintext. The backup
does not include AI route configuration by default, credentials, or other external files.
If the route configuration is stored inside the mounted volume, append
`--ai-config .local/ai.json` to the encrypted backup command to include it.
Review the [AI route backup instructions](BACKUP.md#optional-ai-route-configuration)
for sensitivity, credential, and restore details.
Do not commit backups. Creating a backup inside the volume is not a replacement
for copying it elsewhere.
