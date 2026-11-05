# Finder–Validator–Judge discovery guide

## What a discovery does

Start from a sector or problem objective instead of waiting for an ingested
source to produce a cluster. Finder searches and proposes a concrete business.
Validator independently looks for evidence against it and sends objections or
alternative directions through internal durable mail. Finder can defend, narrow,
pivot or abandon a proposal. Both agents consider services, manufacturing,
intelligence, physical goods, software and other business models across B2B,
B2C and mixed customer relationships.

The default limit is 100 individual submissions shared by Finder, Validator and
Judge. Research/model calls inside a turn have separate limits. Validator accepts
Finder's exact immutable proposal revision after evidence checks; the agreement
is then mailed to Judge. Only Judge approval completes a new run as converged.
Validator's suggested niche must first be adopted by Finder and reviewed again.

Judge defaults to Ollama `kimi-k3:cloud` in a fresh Claude Code session, with its
own frozen provider/model and usage accounting. It examines economics after
overhead and founder effort, opportunity cost, observed buying transactions,
competitive pressure, fit to the original objective, and convergence by exhaustion.
Honest reporting and correct arithmetic alone do not make an opportunity worthwhile.

Judge can approve, request revision, or reject/no-go. A revision returns to Finder
and then Validator; a no-go requires a materially different opportunity within the
original objective. The rejection and its reasons remain in context and the report.
The agents cannot reset turn/time/spending limits after a rejection. If no approved
opportunity is found before a limit, the run ends with a partial report, not approval.
Approval is a research judgment, not proof of willingness to pay.

Objections are scoped to the business they challenge. A cited Finder pivot or
abandonment archives the named prior-business objections; a new opportunity
after Judge no-go archives that rejected business's objection set. Archival
does not claim the old objections were resolved: immutable reviews and rejection
history keep them intact. Repeating their stable IDs later does not resurrect
requirements to prove an abandoned business viable. New-business objections use
new IDs and still block approval until properly addressed.

The report's main evidence section uses the final proposal, its latest
Validator review and current Judge verdict. Repeated claims and citations are
collapsed. Next human tests
use the latest review of the exact proposal when it supplies a checklist,
otherwise the final proposal. A current Judge checklist takes precedence. Tests
from abandoned directions remain under their original turns in the history.

Completion distinguishes convergence, continued disagreement and insufficient
evidence. Failed or cancelled discoveries preserve partial findings. A report
states the actual stop reason and remaining objections. Agreement is research
consensus between agents, not verified demand, profitability or legal clearance.

## Runtime and credentials

All reasoning roles run as **Claude Code subprocesses**, following Video
Swarm's agent-harness pattern. The application owns durable tasks, internal
mail, tools, budgets and report generation; Claude Code owns each role's model
conversation. In the start form, choose a provider and model separately for
Finder and Validator:

| Provider | Model ID example | Host configuration |
|---|---|---|
| OpenRouter | `deepseek/deepseek-v4.1-flash` | `OPENROUTER_API_KEY` |
| Anthropic | `sonnet` or a native Claude model ID | Host Claude Code login or `ANTHROPIC_API_KEY` |
| Ollama | An exact installed/cloud Ollama model tag | `ollama` executable and configured Ollama service |

Prefer a different model family for Validator to reduce shared blind spots.
Providers and models can be selected independently. Separate sessions of the same
model do not supply model diversity; reports flag identical selections. Existing
run snapshots retain the original choices.

All three choices use Claude Code; this is not three different reasoning loops.
The default for new discoveries is OpenRouter with interactive DeepSeek V4.1
Flash. Provider credentials are configured on the host and never submitted as
form fields or saved in a discovery snapshot. Claude's built-in filesystem,
shell and unrelated integrations are not part of the research toolset.

**Batch suffixes are not accepted for Claude Code discoveries.** A dependent
interactive tool conversation needs a model response before research and the
next handoff can proceed. Use `deepseek/deepseek-v4.1-flash` rather than its
`:batch` variant. The application rejects that mismatch explicitly; it never
silently removes the suffix or switches a provider. Existing schema-1.0 batch
records retain their historical execution configuration and remain inspectable.

Configuration schema `1.1` added `runtime`, `finder_provider` and
`validator_provider`. New schema `1.2` adds `judge_enabled`, `judge_provider` and
`judge_model`, defaulting to enabled, Ollama and `kimi-k3:cloud`. Explicit schema
1.0/1.1 configurations keep Judge disabled unless explicitly configured; stored
historical records are not rewritten. Legacy batch execution cannot enable Judge.
Old schema-1.0 snapshots without `runtime` are interpreted as `openrouter_batch`.
All role selections,
connectors, maximum turns, cost ceilings and call limits are frozen when a new
discovery starts. Changing deployment defaults affects subsequent discoveries.

Role providers can differ: for example, Finder on Ollama and Validator on
Anthropic. Choose model IDs appropriate to each provider. Do not place
credentials in objectives, constraints, briefs, evidence or committed files.

General search is explicitly configured:

| Connector | Configuration / purpose |
|---|---|
| `web_search` | Tavily general search; requires `TAVILY_API_KEY` |
| `web_fetch` | Bounded HTML/text and page-addressable PDF retrieval; Poppler required |
| `duckduckgo` | DuckDuckGo HTML general search |
| `bing` | Independent Bing RSS general search; no API key |
| `search_auto` | Explicit DuckDuckGo → Bing fallback on rate limit, timeout, denial or source error |
| `hacker_news` | Search/fetch public Hacker News material |
| `github` | GitHub public research with `GITHUB_TOKEN` |
| `stack_exchange` | Public Stack Exchange research; optional `STACKEXCHANGE_KEY` |
| `local_corpus` | Read-only search of existing local cluster/item records |

New runs default to `DISCOVERY_CONNECTORS=search_auto,web_fetch` (no search API key).
Tavily remains available with `web_search,web_fetch` and `TAVILY_API_KEY`.
`search_auto` makes at most one attempt at each backend; every attempt has its own
operation key, quota accounting, evidence provenance and cooldown. The tool response
uses schema 1.1 with `requested_connector` and `search_attempts`; existing schema-1.0
single-backend responses remain supported. Empty successful results do not trigger
fallback. Exhausting the quota prevents the next backend call. Local cooldown denials
remain audited without spending a source-call slot. No challenge is bypassed.
Connector availability and settings appear at `/api/v1/connectors/` and the start form.
Existing stored connector selections and run histories are unchanged.

`web_fetch` connector version 1.1 adds PDF extraction via Poppler (`brew install poppler`
on macOS; `poppler-utils` is installed in Docker). PDFs are bounded to 20 MB and ten
pages / 120 KB extracted text per call, with subprocess timeouts. Use a URL fragment
`#page=21` or `#page=21-30` to retrieve a later page window. Each readable page is an
independently citable evidence record with its page URL, page count, source-file SHA256,
extraction method and next-page link. These metadata are visible to the model. The
ResearchDocument schema remains 1.0 because page locators live in the existing metadata.
HTML captures also expose up to 30 linked PDF URLs in metadata, so agents can open
annexes linked by a regulator. Original public-IP checks, pinned DNS, TLS verification
and redirect validation apply.
Scanned pages with no text return an explicit OCR-needed outcome; corrupt, encrypted,
oversized and timed-out files are visible errors, never successful empty evidence.
HTML/text keeps the 200 KB bound. A source 429 gets a bounded cooldown (numeric
Retry-After is honored), scoped to its host, so a datospublicos limit cannot block
unrelated government/regulator sites.

Search excerpts are leads. Decisive claims must cite captured page/source
material, and acceptance requires demand and competitor evidence from distinct
source origins, with independent Validator research. Cached material retains
its original retrieval timestamp.

## Admin

- `/admin/discovery/discovery/`: list and filter discoveries.
- `/admin/discovery/discovery/start/`: start from an objective, constraints,
  selected connectors, role providers/models and budgets.
- `/admin/discovery/discovery/<id>/review/`: inspect status, costs, threaded mail,
  immutable submissions, evidence and provider-operation status.
- The detail page provides cancellation and final Markdown report download.
- Once a discovery is completed, failed or cancelled, a user with change
  permission can record a human verdict (`good`, `weak`, `wrong`, `untested`;
  `wrong` requires notes) at the top of the review page. Verdicts are
  append-only: the newest is current and earlier ones remain listed. Each
  verdict records the final proposal and Judge verdict current at the time,
  and recording one regenerates the stored report.

Source/model text is escaped in the interface. Reading the operator page does
not mark an agent's assigned task complete. Refresh the detail page to inspect
persisted progress while Claude Code researches and prepares a handoff.

## API

The API uses the existing `Authorization: Bearer <application-api-key>`
authentication. This is an application key created through the API-key admin,
not the OpenRouter provider key. All endpoints require authentication.

The request envelope remains version 1.0, configuration is version 1.2, the
discovery detail response is version 1.2 (adding `human_verdict`, the latest
verdict or null, and `verdict_count`), and report responses are version 1.1. Responses include Judge execution
settings, the latest verdict and judgment history. Judge approval is tied to
the exact latest proposal and accepted validation; stale approval is not reused.

Start with a stable idempotency key. Reusing it with the same request returns
the same discovery; a different request conflicts rather than starting another
paid run.

```http
POST /api/v1/discoveries/
Authorization: Bearer <application-api-key>
Idempotency-Key: travel-research-001
Content-Type: application/json

{
  "schema_version": "1.0",
  "objective": "Look for opportunities in the travel space",
  "constraints": "Argentina; include service and intelligence businesses",
  "config": {
    "schema_version": "1.2",
    "runtime": "claude_code",
    "finder_provider": "openrouter",
    "validator_provider": "anthropic",
    "max_turns": 100,
    "max_cost_usd": "10.00",
    "max_duration_s": 86400,
    "finder_model": "deepseek/deepseek-v4.1-flash",
    "validator_model": "sonnet",
    "judge_enabled": true,
    "judge_provider": "ollama",
    "judge_model": "kimi-k3:cloud",
    "connectors": ["search_auto", "web_fetch"]
  }
}
```

Other endpoints:

| Method/path | Result |
|---|---|
| `GET /api/v1/discoveries/` | Newest discoveries; optional `status`, `limit`, `offset` |
| `GET /api/v1/discoveries/<id>/` | State, outcome, counters, safe frozen config and report readiness |
| `GET /api/v1/discoveries/<id>/messages/` | Paginated internal mail, oldest first |
| `GET /api/v1/discoveries/<id>/evidence/` | Paginated captured evidence |
| `GET /api/v1/discoveries/<id>/report/` | Markdown attachment; 409 until ready |
| `POST /api/v1/discoveries/<id>/cancel/` | Idempotent cancellation |
| `POST /api/v1/discoveries/<id>/verdict/` | Record a human verdict `{"schema_version": "1.0", "verdict": "good", "notes": "..."}`; 201 with the record, 409 unless terminal, 400 on contract errors. `recorded_by` is null for API-key callers |
| `GET /api/v1/connectors/` | Capability and safe configuration status |

Paginated responses contain `count`, `limit`, `offset`, `next_offset` and
`results`. Limits are 1–200. An omitted limit defaults to 50. Report downloads
are Markdown text rather than executable HTML. Cancellation prevents new calls;
work already submitted may still incur a charge.

## Benchmark runs

`discovery/eval/objectives.json` is the versioned benchmark set (twelve
objectives with a reviewer rubric each). `manage.py run_discovery_benchmark`
starts one discovery per objective through the normal idempotent start
service with key `benchmark:<tag>:<objective_id>`, and records
`benchmark_tag` and `benchmark_objective_id` on each discovery.

```sh
python manage.py run_discovery_benchmark --tag baseline-2026-11            # dry run: prints plan
python manage.py run_discovery_benchmark --tag baseline-2026-11 \
    --authorize-spend --max-total-usd 120                                   # starts discoveries
```

- Without `--authorize-spend` nothing starts; the command prints the frozen
  config, role prompt hashes, the ceiling sum and each idempotency key.
- `--max-total-usd` is required to start and the command refuses when
  `max_cost_usd × objectives` exceeds it.
- `--objective <id>` (repeatable) runs a subset; `--config-json '{...}'` is
  merged over deployment defaults and validated before anything starts.
- Re-running with the same tag never starts a second discovery for an
  existing key. The command does not wait; workers run the discoveries.
- The review page shows the objective's rubric beside the verdict form.

`manage.py score_discoveries --tag <tag>` (or `--discovery <id>`, repeatable;
optional `--since YYYY-MM-DD`) computes the quality metrics for every terminal
discovery selected, lists skipped non-terminal ones and persists one
append-only `DiscoveryQualityRun` with per-metric mean, median, min, max, sum,
count and `None` count. `--compare-to <run id>` prints per-metric deltas and
warns when prompt hashes, configurations or the metrics schema differ.
`--format json` prints the persisted run.

## Recovery and billing

Messages, assignments, completed turns and exact proposal references are
persisted. Reading mail is not task completion. Transactional dispatch records
allow committed tasks to be published after temporary broker failures. Leases
and fencing prevent a stale worker from committing another handoff.

Acceptance failures on an immutable proposal's evidence set must become a
Validator challenge, so Finder can revise the proposal. Adding citations only
to a Validator review does not repair Finder's sources. The tool reports this
recovery direction instead of encouraging repeated acceptance attempts.
Unknown citation errors include full captured-source ID/title/URL candidates
when a prefix matches exactly one source. These are correction hints: unknown
IDs are still rejected, and no citation is silently substituted.

An operator can explicitly authorize a bounded correction of a settled wake
with a UUID `claude_recovery_id` in task context. It opens a distinct ledger/log
identity, preserves the original logs and allows at most two model calls. Prior
unsettled model outcomes block it; reusing the token cannot replay the wake.
This is not an automatic retry or a reset of turn, time or spending limits.
Research requests denied locally during a shared connector cooldown retain
their audit records but do not consume a source-call slot. Actual outbound
requests, including provider-side rate limits, and cached research results still
count. Local-denial responses use schema 1.1 with `request_performed=false`;
historical local denials are recognized by their existing error reason.
For a proven pre-inference executable launch failure, `claude_recovery_mode`
may be `launch_failure`: it restores the original call allowance only when a
prior failed wake records `launch_failed_before_model=true`. Empty output and
an explicit exec failure must establish that no inference started; absent such
proof the billing outcome remains unknown and blocks retries.

External operation records distinguish planned, submitted, completed and unknown
outcomes. A Claude Code process interrupted after inference may have incurred a
charge; do not blindly start a second billed attempt. Historical batch jobs use
their saved provider ID when polling rather than creating a second batch. Unknown billed
outcomes require operator reconciliation before another paid attempt. Known
spend and unknown outcomes remain distinct in the final report. Do not infer
zero spend from missing usage.

Inspect discovery detail and worker logs for the error and operation IDs. Keep
provider identifiers and evidence when investigating. A report-render failure
can be retried from persisted records via
`discovery.orchestrator.regenerate_report(discovery_id)` without another model
call. Cancellation cannot recover money already billed by a provider.

## Adding a connector

Implement the `Connector` interface in `discovery/connectors/base.py`:

- `describe()` returns a versioned `ConnectorDescription` with stable ID,
  capabilities, supported filters, safe configuration status and cost bounds.
- `search(query, filters, cursor, limit)` returns a normalized `ResearchPage`.
- `fetch(target)` returns captured source documents where supported.
- `check_configuration()` verifies locally that required settings exist.

Return versioned `ResearchDocument` values with stable IDs, canonical URLs,
retrieval/publication timestamps, provenance, content and content hashes. Declare
pagination/truncation, unsupported capabilities and typed failure outcomes.
Connectors are read-only: the orchestration/research service records evidence and
charges; connectors never classify, cluster or mutate discovery state.

Register the implementation by its trusted dotted class path in
`DISCOVERY_CONNECTOR_CLASSES`. This is a deployment allowlist; agents cannot
import code or supply credentials. Do not disable public-fetch address checks
to reach a private source: that requires a separately scoped transport.

## Prototype verification and hardening

Prioritize one working start → search → Finder brief → Validator challenge →
Finder revision → report flow. Use focused offline provider fixtures and a real
PostgreSQL database for prototype smoke checks. Broad regression coverage,
concurrency failure injection and coverage gates follow in the final hardening
phase. Live paid acceptance runs require explicit authorization and a budget.

The earlier ingestion/clustering/investigation/ideation screens and schedules
remain available. New Discovery runs do not require legacy ingestion. Existing
enabled schedules are not automatically disabled by the migration; inspect
periodic tasks before providing credentials for unattended work.


### Repeatable offline prototype demonstration

With the development database migrated and Redis running:

```sh
.venv/bin/python scripts/smoke_discovery.py
```

This historical batch-path smoke uses real PostgreSQL, an extension-registry
fixture connector and `httpx.MockTransport` for OpenRouter traffic. It explicitly
selects `openrouter_batch`; it does not exercise the new Claude Code launcher. It demonstrates a four-turn
service proposal, saturation challenge, niche pivot and exact-revision agreement
through eight asynchronous model steps. The clearly labelled synthetic discovery
and ignored `.tmp/discovery-smoke-report.md` remain for inspection. These are
fixture conclusions, not market findings. No paid API calls are made.

The local host setup in this checkout uses PostgreSQL on 55432, Redis on 56379
and Django on 8088. Host `.env` addresses differ from Docker Compose service
addresses. Start host processes with:

```sh
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8088
.venv/bin/celery -A config worker --pool=solo --concurrency=1 -l info
.venv/bin/celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Run web, worker and beat in separate terminals. Discovery dispatch repair is
registered in Beat automatically; legacy ingestion schedules are not seeded.
Provider-reported costs and configured Tavily charge estimates are labelled
separately in reports; both consume the budget conservatively. An outstanding
remote batch is an unsettled possible charge, never assumed free.


### Host runtime requirements

Claude Code must be installed and executable by the Celery worker for every
provider. Ollama selection additionally requires the Ollama launcher. For
Anthropic, authenticate Claude Code on the same host account used by the worker,
or set its API key explicitly. An interactive login is a human setup step, not
an action performed by the discovery agent.

The repository's original Python Docker image does not provision Claude Code or
Ollama. Run the web/worker on a configured host for this prototype, or explicitly
build an image that supplies those runtimes and authentication. A missing binary
or unavailable selected provider is a visible configuration failure, not a reason
to route to a different backend.

Default provider/model settings:

```dotenv
DISCOVERY_RUNTIME=claude_code
DISCOVERY_FINDER_PROVIDER=openrouter
DISCOVERY_VALIDATOR_PROVIDER=openrouter
DISCOVERY_FINDER_MODEL=deepseek/deepseek-v4.1-flash
DISCOVERY_VALIDATOR_MODEL=deepseek/deepseek-v4.1-flash
DISCOVERY_JUDGE_ENABLED=True
DISCOVERY_JUDGE_PROVIDER=ollama
DISCOVERY_JUDGE_MODEL=kimi-k3:cloud
```


### Claude Code execution decisions and focused checks

Finder resumes its per-discovery Claude session. Validator starts a fresh session
for each review, receiving the latest proposal, objections and evidence. The
supervisor renews the task lease while Claude runs, enforces the deadline and
research-call limits, and stops the process family on cancellation. The mail
handoff is committed after wake usage is settled, so a following turn cannot
race ahead of accounting.

Claude receives only the discovery MCP research/submission tools: built-in file,
shell, browser and subagent tools are disabled. The installed Claude Code version
suppresses even explicitly configured MCP servers under `--safe-mode`, so this
implementation uses `--restricted`, empty settings sources, an explicit system
prompt, disabled slash commands and strict MCP configuration instead. This was
verified with the actual installed CLI, not inferred from its help text.

OpenRouter uses a loopback Anthropic-Messages gateway, exact model routing,
per-call reservations and an ephemeral child credential. The OpenRouter key
never enters Claude's environment or prompt. Anthropic uses an API key or the
worker account's existing Claude login. Ollama uses `ollama launch claude` with
isolated launcher preferences; local models must already be installed. Discovery
does not log in to accounts, pull models or change global Ollama preferences.

OpenRouter costs are provider-reported when available and explicitly estimated
otherwise. Native Anthropic uses Claude Code's spending cap and reported usage;
that cap is checked at client turn boundaries and is not a strict prepaid wallet.
Subscription valuations and Ollama cloud-plan charges are not verified invoices.
Unsettled operations remain visible rather than being assumed free.

Ollama Cloud usage is estimated from observed input, cached-input and output
tokens using published model rates, recorded with their source and verification
date. Claude Code's own dollar valuation is not an Ollama bill. Its reported
cached tokens are a separate category, not subtracted from uncached input twice.
Actual account credit deductions still require reconciliation with Ollama.

Ollama's dollar limit is checked between agent wakes. A wake can contain several
model requests and exceed the remaining estimate before usage is returned.
It is a soft application limit, not a provider-enforced billing cap.

Reconcile historical completed wakes without making model calls:

```sh
.venv/bin/python manage.py reconcile_discovery_costs --discovery DISCOVERY_ID
.venv/bin/python manage.py reconcile_discovery_costs --discovery DISCOVERY_ID --apply
```

The default is a dry run. Apply is idempotent and limited to terminal runs;
repeat `--discovery` to reconcile predecessors as well. Each run keeps its own
charges, so continuation totals must include their original runs. Unsettled
wakes remain explicitly incomplete. The command preserves Claude's original
valuation and records the published pricing source/date alongside the estimate.
New runtime snapshots use version 1.3 and cloud wake accounting uses version 1.1;
historical model/provider snapshots stay intact while their operation responses
gain the dated reconciliation basis. Refresh a persisted report with
`discovery.orchestrator.regenerate_report` after applying reconciliation.

Cloud models need a checked rate entry in `discovery/accounting.py`; unknown
tags are rejected during preflight until their published rates are registered.

Repeat the real-harness, provider-free demonstration with:

```sh
.venv/bin/python scripts/smoke_claude_discovery.py
```

It runs the installed Claude Code executable against simulated OpenRouter
Messages responses, real MCP tools and PostgreSQL, through four turns and twelve
model requests. It checks Finder session reuse, independent Validator sessions,
research, a saturation challenge, a niche pivot and final agreement. No paid
requests are made. Its report is `.tmp/claude-discovery-smoke-report.md` and is
clearly labelled synthetic. Anthropic/Ollama configuration and launcher contracts
were checked without invoking their paid inference or changing credentials.
