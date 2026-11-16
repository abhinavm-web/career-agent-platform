# Optional model router and requirement analyst

The local dashboard and `ai-analyze` CLI command can send one job's title/description and candidate-verified fact IDs/statements to one explicitly selected model. The returned requirement quotes must occur verbatim in the description; cited fact IDs must exist. Suggested relationships and explanations remain **unverified model interpretations**, not eligibility decisions, skill deficits, hiring probabilities or verified equivalence. No facts, rankings, packets, approvals or applications are modified. Results are displayed, not persisted; CLI output can be redirected to a private file.

The dashboard can also request résumé wording suggestions for a selected job and route with the same explicit consent. Each suggestion displays the verified source fact and an exact job excerpt. IDs and excerpts are checked, but this does **not** verify that generated wording faithfully represents the fact; fabricated or misleading wording remains possible. Suggestions are not automatically saved or inserted in a packet. Choose **Save unverified wording for review** to copy one into the career workspace with a snapshot of its source fact and job excerpt. The app checks that the source fact is still verified and that the excerpt is still present; a stale source must be regenerated. Review and edit the wording against your records, then separately verify it before selecting it for a new draft. Its source snapshot is retained in private workspace history, not printed in the résumé. This is not an automated semantic-fidelity check or a complete document editor.

The suggestion view also flags selected changes to numeric text and limits, wording in clauses containing negation, stronger role or outcome words, and newly mentioned terms from a small fixed list of technologies. These simple rules are review prompts, not an assessment of truth. They can flag accurate rewording or technology aliases and miss other skills, employers, responsibility changes, spelled-out numbers, or invented claims. **No warning does not mean a suggestion is supported.** Every suggestion still requires the same separate candidate verification and packet approval.

Run `python scripts/evaluate_tailoring.py` from the repository root to measure these prompts on the included hand-labeled fictional examples. The script mocks provider output and passes each case through the real suggestion schema/citation validation and warning logic. It reports flagged and unflagged unsupported examples and flagged supported examples, including deliberate blind spots. This small constructed set is a regression fixture, not a representative model-quality or hiring assessment, and it does not test live inference. The full per-case report stays in the terminal unless you explicitly redirect it.

To inspect a real model's tailoring responses without sending personal data, run `python scripts/evaluate_live_tailoring.py --route ROUTE --acknowledge-provider-requests`. It sends up to three fixed fictional jobs and facts to that selected provider, uses the normal citation and warning checks, and prints each source fact, job text and generated suggestion for human review. It does not automatically grade truth or establish model quality. Cloud requests can incur charges; a local server may forward requests. Configured daily limits and optional metadata-only history apply to each request, using the default workspace database or the path supplied with `--db`. The report is printed, not saved by the script; redirect it only where appropriate. It stops if the limit is reached or the configured destination changes.

The same review is available in **AI analysis → Review fictional résumé tailoring cases**. Open the preview, select a route and explicitly consent before sending up to three requests. Results show the source, job excerpt, proposed wording and any warnings; nothing is saved to the career profile. Partial results and limits are displayed, and changing the route invalidates consent. The selected provider and local limits have the same implications as the CLI.

Requirement analysis asks models to select short source IDs from up to 100 distinct posting lines, then resolves those IDs to unchanged source text. This keeps long excerpts out of schema enums, avoiding provider schema-compilation limits. Ollama, LM Studio and Anthropic receive structured-output schemas; all providers undergo independent citation validation. Tailoring schemas constrain supplied fact IDs and posting excerpts. The line restriction may omit requirements beyond those lines; inspect the original posting. The same independent validation applies to every provider, including exact excerpt checks. Whitespace-only differences are resolved back to the original contiguous source span; changes to words, metrics or punctuation are rejected. Small local models can still return inaccurate wording, irrelevant excerpts or fail to follow the schema; a successful response is not a quality guarantee. Some local models may simply repeat the source fact.

The LangGraph application workflow remains deterministic and now uses durable SQLite checkpoints through the dashboard and CLI `workflow` command. This AI analyst is independently opt-in; it does not silently add model calls to scheduled discovery or application drafting. See [automation and durable runs](AUTOMATION.md).

Career profile also offers a career assistant. Each request requires a selected route and explicit consent to send the question and current session's conversation to that provider; sharing candidate-verified fact statements is a separate opt-in. Source notes and candidate name are not included as separate fields, though conversation and facts can themselves contain private information. Answers are unverified advice and cannot edit the profile. Up to six exchanges are kept in the browser until cleared or the page is reloaded; they are not persisted. Changing providers sends the existing conversation to the newly selected provider on the next consented request.

In **Career profile → Import and review résumé text**, the optional **Suggest structured facts with AI** action sends the entire visible résumé text (up to 20,000 characters) to the chosen route only after separate consent. Review and remove contact, immigration or other sensitive information before consenting; a local model server may itself forward requests. The model selects numbered source passages and proposes categories; the app resolves every selection to the original text. Duplicate or unknown source IDs are rejected; schema and exact excerpt checks reject invented or altered text, but do not establish truth, relevance or correct classification. Results remain in the browser; choose an excerpt to put it into the existing fact editor, review the suggested category and add a private evidence note, save unverified, then separately verify it. Changing the text or route discards outdated suggestions. Nothing is saved or verified automatically. OCR for scanned PDFs remains unsupported.

## Setup

Install optional provider SDKs:

```powershell
./.venv/Scripts/python.exe -m pip install -e '.[ai]'
```

Create `.local/ai.json` (ignored by Git). Keep only the routes you intend to use; replace model/deployment placeholders with IDs available in your account or local server. Model availability is not established by adding a route.

```json
{
  "claude": {"provider": "anthropic", "model": "YOUR_CLAUDE_MODEL_ID", "key_env": "ANTHROPIC_API_KEY", "max_tokens": 1500, "daily_request_limit": 20},
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

### Local request limits and history

History is off by default. Set `record_history: true` on a route to opt in without a limit, or set `daily_request_limit` to an integer from 0 to 10,000 to opt in and cap requests; omitting the limit means no local cap, and 0 blocks that route. For enabled routes, the app atomically reserves one attempt before each consented dashboard test, chat, requirement analysis, résumé wording or résumé fact suggestion, and CLI `ai-analyze` using the same workspace database. Reservations are counted by route name and UTC calendar day; failed, interrupted and locally rejected attempts still count. A process interrupted during a request remains `running` in history and its reservation is retained. Requests are rejected before a provider call when the cap is reached.

Open **Local AI request history and limits** in AI analysis to see recent request metadata and today's counts. The local SQLite journal stores route name, provider, model, purpose, timestamps and completed/failed/running state. It does not store prompts, responses, job IDs, private facts, provider errors, credentials, token counts or costs. It is private plaintext workspace data and is retained with the database and backups. `completed` indicates the app accepted a response, not that a model's advice is correct.

This cap is **not a dollar, token or account-wide budget**. Different route names, separate workspace databases, direct provider use, configuration changes, or restoring an older database can bypass or reset it. Set provider-side spending limits independently. No usage or cost is estimated by this journal.

AWS uses the normal SDK credential chain (prefer a signed-in profile/role). No credentials are read from JSON. An explicitly configured region is required; inference profiles may route across regions according to AWS configuration. Azure supports key-authenticated Azure OpenAI v1 chat completions, not arbitrary Foundry endpoints or Entra ID yet. Local routes require literal loopback HTTP addresses. Optional `key_env` can name a local server token; otherwise a dummy local credential is used. A loopback endpoint does not guarantee local inference: Ollama cloud models, proxies and LM Studio remote links may forward requests. Check that server's settings.

Open **AI analysis** in the workspace navigation, select a route and job, and explicitly consent per request. You can also open analysis from an opportunity's detail; it carries the selected job into the analysis screen. At least one verified career fact is required. The selected destination is shown. No provider is contacted merely by loading the page or configuration.

Each dashboard request is bound to the displayed route name, provider, model and destination. If those settings change after routes were loaded, the request is rejected before contacting a provider or consuming a local request allowance. Reload routes, inspect the destination and consent again. The check cannot establish whether a loopback server forwards data elsewhere or whether provider-side settings have changed.

**Test model connection** works without a saved job or verified facts. Refresh routes, select one, and separately consent to send a fixed synthetic prompt. It sends no candidate or job data; cloud requests may incur charges. The output is not displayed or saved. A successful response does not prove future availability, response quality, or provider privacy settings. The displayed SDK and credential indicators are local checks only; AWS credentials remain unknown until used. They do not verify a key or model access. If a route or key changes, refresh routes and restart the server when changing its environment.

For a configured Ollama or LM Studio route, **Check local model IDs** explicitly reads that loopback server's `/v1/models` endpoint without sending a job, profile or inference prompt. It sends any configured local route credential to that server. The list is bounded and may be incomplete, stale, include unloaded models, or omit working aliases; it cannot prove model usability or privacy. A local server may forward requests. This metadata read does not count toward the app's inference-request history or daily limit and does not edit the route configuration. Use an advertised ID in your local configuration after reviewing it, then test the route separately.

**Test fictional analysis** is a separate consented request using a fixed fictional job and fictional career fact. It exercises the same evidence and response validation as real requirement analysis without reading a saved job or candidate profile. Results are displayed only for review and cannot be saved as a candidate fact or application. The selected provider receives the fictional sample; cloud calls may incur charges and a local server may forward requests. A passing sample is not a quality benchmark. It counts toward the route's local daily limit and, when enabled, appears in request history as `sample_analysis` without prompts or responses.

The result identifies the configured route model separately from the optional model name reported by the provider. Aliases can resolve to another name; the provider's label is unverified and may be unavailable or omitted if malformed. The local request journal retains only the configured model, not the provider-reported name or response.

Alternatively, use the CLI:

```powershell
career-agent --db .local/demo.sqlite3 ai-analyze JOB_ID --facts .local/facts.json --route claude --acknowledge-data-sharing
```

Use `CAREER_AGENT_AI_CONFIG` to point to another private JSON file. Unknown fields (including inline secrets) are rejected. Candidate names, contact fields and private source notes are excluded as separate fields, but verified statements and posting text can contain personal information. Inspect them before consenting.

## Reliability and limits

- One chosen route per call; no retries, cross-provider fallback, tools, web browsing or model-triggered actions. This avoids unexpected duplicate charges or data destinations. Failures remain failures.
- Base analysis input cap: 32,000 characters; numbered source maps may expand analysis or resume-extraction payloads to 48,000 characters, with explicit rejection instead of silent truncation. Output cap: configurable 128–16384 tokens. SDK connection/read timeouts: 10/60 seconds (inactivity bounds, not a strict wall-clock deadline). One dashboard analysis at a time per server process.
- Anthropic, Azure, Ollama and LM Studio HTTP responses (including errors) are bounded to 1 MiB before SDK buffering. Requests ask for identity encoding; a server that nevertheless compresses a response is rejected because compressed bytes do not bound decompressed memory. A rejected response may still have incurred provider charges. This HTTP cap does not apply to Bedrock’s separate SDK transport.
- Completions must be nonempty assistant text of at most 32,000 characters. Anthropic accepts known thinking/redacted-thinking blocks alongside a completed text answer, but never returns their contents to the app. Tool and unknown blocks remain rejected. Bedrock rejects unexpected non-text blocks; OpenAI-compatible routes reject refusals and tool/function calls. Bedrock is checked after SDK parsing, not bounded during transport.
- Malformed JSON, invented quotes/IDs, excess fields, incomplete generation and tool calls are rejected. Prompt injection cannot invoke tools; it can still corrupt interpretations, which need human review.
- Keys and raw provider errors are not returned to the dashboard. Fixed diagnostic categories can indicate missing credentials, connection or timeout problems, rejected access, missing model/endpoint, rate limits, provider server errors, or incomplete responses; they are hints, not a guarantee of root cause. No app prompt logging, telemetry or remote tracing is added. Provider-side retention/logging depends on your account; do not enable SDK debug logging with real candidate data.
- SDK contract tests use simulated transports and fictional credentials; they are not live model quality benchmarks. No paid cloud calls or installed local model inference were validated during this increment.
- Still needed for model calls: durable AI jobs/cancellation, provider-reported token usage and account-wide cost budgets, broader provider health diagnostics, and evaluation against larger independently reviewed and representative datasets. The local metadata journal does not record token usage or spending. Wording suggestions retain their cited source when saved for review, but need explicit candidate verification. Deterministic workflow persistence and opted-in scheduled JobSpy discovery are implemented separately.

## API references

- [Anthropic Python SDK](https://platform.claude.com/docs/en/api/sdks/python)
- [Azure OpenAI v1](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)
- [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio compatibility](https://lmstudio.ai/docs/developer/openai-compat)

## AI document revisions

Application studio includes **AI resume and cover letter**. Load configured models
and saved revisions, choose a model, enter revision instructions, and consent to
sharing the posting, verified fact statements, instructions and previous current
revision. The model rewrites both documents; it does not merely copy the fact bank.
Names, contact lines and private source notes are not sent as separate fields.
They can still appear in user-entered instructions or fact text.

Each passage cites verified fact IDs. Citations and automatic wording warnings
are review aids, not semantic verification. Every revision is saved separately
in the workspace database with source statements and model metadata. The user
must compare both documents against their facts and confirm accuracy before
text download. New revisions require fresh review; source/profile changes or
newer revisions block export of an old revision. Existing deterministic packets,
profile verification and application submission state are unchanged. This is a
text revision workflow; formatted Word/PDF export remains available for the
existing verified-fact packets, not these AI document revisions.

Anthropic credentials remain in `ANTHROPIC_API_KEY` in the server environment.
The optional Anthropic route setting `thinking_disabled: true` explicitly asks
for text-only generation on compatible models. Leave it absent for models that
do not support disabling reasoning. Unsupported response blocks remain rejected,
not silently dropped. Model IDs must be available to the configured account.

### Build a new resume and discover missing experience

The AI document studio defaults to building a new resume without sending the
previous draft. The model can reorganize verified facts into a summary,
experience, projects, skills, education and certifications, omitting unsupported
sections. Uncheck the option to revise the previous current draft instead.

**Find experience to add** uses the chosen model to identify exact posting
excerpts and suggest existing evidence. These may be incomplete or incorrect
relationships; an empty evidence list is not a finding that the candidate lacks
experience. The interview asks for candidate-authored statements about relevant
work absent from the profile. Answers are saved as unverified facts. A separate
candidate confirmation is required before they can enter a generated resume.
The question list is session-only; saved answers and fact verification persist.

**Coverage review** has an 80% target. It measures the proportion of recognized
requirement-section excerpts for which the user selected a supporting resume
passage. It is explicitly a user-reviewed subset measure, not a whole-job match,
ATS score, eligibility decision, or independent semantic verification. Duplicate
excerpts are deduplicated. Missing/unrecognized sections are not assumed covered;
no percentage is shown when none can be extracted, and parser limits are flagged.
The selection and result are saved with the reviewed revision. Below-target
exports are permitted: truthful omissions are preferable to unsupported claims.


The Anthropic adapter requires SDK 0.125 or later and uses `output_config.format`
with `transform_schema`. Unsupported constraints remain enforced by the app after
completion; schema-valid output does not establish semantic accuracy. See the
[Anthropic structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

The deterministic requirement comparison recognizes compound headings such as
"Required Skills & Experience". For verified facts without saved keywords, it can
suggest literal shared source phrases; it labels those links separately and never
adds keywords or verifies equivalence. Unrecognized requirement headings make
coverage incomplete and prevent reporting the 80% coverage target as met.

PDF import removes standalone page counters and joins clear lowercase line
continuations. Review the extracted text against the original PDF, especially
role boundaries and wrapped qualifiers. Newly imported facts stay unverified;
conflicting metrics or scope in an updated resume require candidate reconciliation.
Tracker archiving now hides a role from the active opportunity board, and restoring
it reopens its pre-application tracker stage. Submitted application history remains
independent from whether the posting is archived.


## Reviewed AI document downloads

A current, reviewed AI revision offers PDF, DOCX and text downloads for both the
resume and cover letter. Exports use exactly the reviewed document text plus the
snapshot-bound profile header; no model request occurs during export. Private
source notes, fact IDs, prompts, coverage annotations and review warnings are
excluded. Downloaded DOCX files remain editable outside the application.

PDF rendering disables scripts and all network access. DOCX renders text as
literal paragraphs. Exports recheck the profile, posting, review and latest
revision after rendering: changed or superseded drafts must be reviewed again.
These downloads are separate from the verified-fact application packet; neither
export path submits an application or modifies candidate facts.

For comprehensive AI documents, a route budget of 8,192 tokens can leave room for reasoning and a full answer. Provider model limits still apply. Truncated responses remain rejected; no partial document is saved or exported.
