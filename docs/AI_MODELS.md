# Optional model router and requirement analyst

The local dashboard and `ai-analyze` CLI command can send one job's title/description and candidate-verified fact IDs/statements to one explicitly selected model. The returned requirement quotes must occur verbatim in the description; cited fact IDs must exist. Suggested relationships and explanations remain **unverified model interpretations**, not eligibility decisions, skill deficits, hiring probabilities or verified equivalence. No facts, rankings, packets, approvals or applications are modified. Results are displayed, not persisted; CLI output can be redirected to a private file.

The LangGraph application workflow remains deterministic and now uses durable SQLite checkpoints through the dashboard and CLI `workflow` command. This AI analyst is independently opt-in; it does not silently add model calls to scheduled discovery or application drafting. See [automation and durable runs](AUTOMATION.md).

## Setup

Install optional provider SDKs:

```powershell
./.venv/Scripts/python.exe -m pip install -e '.[ai]'
```

Create `.local/ai.json` (ignored by Git). Keep only the routes you intend to use; replace model/deployment placeholders with IDs available in your account or local server. Model availability is not established by adding a route.

```json
{
  "claude": {"provider": "anthropic", "model": "YOUR_CLAUDE_MODEL_ID", "key_env": "ANTHROPIC_API_KEY", "max_tokens": 1500},
  "azure": {"provider": "azure", "model": "YOUR_DEPLOYMENT_NAME", "base_url": "https://YOUR_RESOURCE.openai.azure.com/openai/v1", "key_env": "AZURE_OPENAI_API_KEY"},
  "aws": {"provider": "bedrock", "model": "YOUR_CONVERSE_MODEL_OR_INFERENCE_PROFILE_ID", "region": "us-east-1"},
  "ollama": {"provider": "ollama", "model": "YOUR_INSTALLED_MODEL", "base_url": "http://127.0.0.1:11434/v1"},
  "studio": {"provider": "lmstudio", "model": "YOUR_LOADED_MODEL", "base_url": "http://127.0.0.1:1234/v1"}
}
```

Set credentials in the process environment before starting the app. For Anthropic, enter the key without writing it into a command or shell history:

```powershell
$env:ANTHROPIC_API_KEY = Read-Host 'Anthropic API key' -MaskInput
```

`-MaskInput` requires PowerShell 7.1+. Use an environment/secret manager with older shells. Never paste credentials into chat, browser forms, config JSON or Git. The app does not load `.env` files. Restart a running server after changing its environment. Configure a provider-side spending limit as well; output token limits are not dollar budgets.

AWS uses the normal SDK credential chain (prefer a signed-in profile/role). No credentials are read from JSON. An explicitly configured region is required; inference profiles may route across regions according to AWS configuration. Azure supports key-authenticated Azure OpenAI v1 chat completions, not arbitrary Foundry endpoints or Entra ID yet. Local routes require literal loopback HTTP addresses. Optional `key_env` can name a local server token; otherwise a dummy local credential is used. A loopback endpoint does not guarantee local inference: Ollama cloud models, proxies and LM Studio remote links may forward requests. Check that server's settings.

Open **AI analysis** in the workspace navigation, select a route and job, and explicitly consent per request. You can also open analysis from an opportunity's detail; it carries the selected job into the analysis screen. At least one verified career fact is required. The selected destination is shown. No provider is contacted merely by loading the page or configuration. Alternatively:

```powershell
career-agent --db .local/demo.sqlite3 ai-analyze JOB_ID --facts .local/facts.json --route claude --acknowledge-data-sharing
```

Use `CAREER_AGENT_AI_CONFIG` to point to another private JSON file. Unknown fields (including inline secrets) are rejected. Candidate names, contact fields and private source notes are excluded as separate fields, but verified statements and posting text can contain personal information. Inspect them before consenting.

## Reliability and limits

- One chosen route per call; no retries, cross-provider fallback, tools, web browsing or model-triggered actions. This avoids unexpected duplicate charges or data destinations. Failures remain failures.
- Input cap: 32,000 characters, with explicit rejection instead of silent truncation. Output cap: configurable 128–4096 tokens. SDK connection/read timeouts: 10/60 seconds (inactivity bounds, not a strict wall-clock deadline). One dashboard analysis at a time per server process.
- Malformed JSON, invented quotes/IDs, excess fields, incomplete generation and tool calls are rejected. Prompt injection cannot invoke tools; it can still corrupt interpretations, which need human review.
- Keys and raw provider errors are not returned to the dashboard. No app prompt logging, telemetry or remote tracing is added. Provider-side retention/logging depends on your account; do not enable SDK debug logging with real candidate data.
- SDK contract tests use simulated transports and fictional credentials; they are not live model quality benchmarks. No paid cloud calls or installed local model inference were validated during this increment.
- Still needed for model calls: durable AI jobs/cancellation, persistent model-run provenance and usage/cost budgets, provider health diagnostics, semantic-quality evaluation fixtures and role-specific model-assisted drafting with claim verification. Deterministic workflow persistence and opted-in scheduled JobSpy discovery are implemented separately.

## API references

- [Anthropic Python SDK](https://platform.claude.com/docs/en/api/sdks/python)
- [Azure OpenAI v1](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)
- [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio compatibility](https://lmstudio.ai/docs/developer/openai-compat)
