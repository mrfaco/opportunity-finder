# Finder–Validator discovery guide

## What a discovery does

Start from a sector or problem objective instead of waiting for an ingested
source to produce a cluster. Finder searches and proposes a concrete business.
Validator independently looks for evidence against it and sends objections or
alternative directions through internal durable mail. Finder can defend, narrow,
pivot or abandon a proposal. Both agents consider services, manufacturing,
intelligence, physical goods, software and other business models across B2B,
B2C and mixed customer relationships.

The default limit is 20 individual agent submissions: Finder turns 1, 3, 5, …
and Validator turns 2, 4, 6, …. Research/model calls inside a turn have separate
limits. The loop can stop early when Validator accepts Finder's exact immutable
proposal revision and evidence checks pass. Validator's suggested niche is not
agreement until Finder adopts it and Validator reviews the new revision.

Completion distinguishes convergence, continued disagreement and insufficient
evidence. Failed or cancelled discoveries preserve partial findings. A report
states the actual stop reason and remaining objections. Agreement is research
consensus between agents, not verified demand, profitability or legal clearance.

## Runtime and credentials

Both roles default to `deepseek/deepseek-v4.1-flash:batch` through OpenRouter.
Set `OPENROUTER_API_KEY` in the ignored `.env` or deployment environment. The
batch runtime uses asynchronous provider jobs. The `:batch` selection is retained
in the discovery snapshot; requests use OpenRouter's batch API rather than a
synchronous fallback. A sequence of dependent agent calls can take hours, so
choose a discovery wall-clock limit that permits queueing. The ordinary default
is 1,800 seconds; increase it deliberately for a longer batch run.

Role models, connectors, maximum turns, cost ceiling and call limits are frozen
when a discovery starts. Changing deployment settings affects future runs rather
than silently changing an existing dialogue. Do not place credentials in objectives,
constraints, briefs, evidence, prompts or committed configuration files.

General search is explicitly configured:

| Connector | Configuration / purpose |
|---|---|
| `web_search` | Tavily general search; requires `TAVILY_API_KEY` |
| `web_fetch` | Bounded public-page retrieval; no separate API key |
| `duckduckgo` | Optional free general web search; explicit selection, no fallback |
| `hacker_news` | Search/fetch public Hacker News material |
| `github` | GitHub public research with `GITHUB_TOKEN` |
| `stack_exchange` | Public Stack Exchange research; optional `STACKEXCHANGE_KEY` |
| `local_corpus` | Read-only search of existing local cluster/item records |

Use `DISCOVERY_CONNECTORS=web_search,web_fetch` for Tavily, or explicitly choose
`duckduckgo,web_fetch` when using the free connector. Connector availability and
required settings are shown by `/api/v1/connectors/` and the start form. Missing
configuration is an error; a failed source is not silently substituted.

Search excerpts are leads. Decisive claims must cite captured page/source
material, and acceptance requires demand and competitor evidence from distinct
source origins, with independent Validator research. Cached material retains
its original retrieval timestamp.

## Admin

- `/admin/discovery/discovery/`: list and filter discoveries.
- `/admin/discovery/discovery/start/`: start from an objective, constraints,
  selected connectors, role models and budgets.
- `/admin/discovery/discovery/<id>/review/`: inspect status, costs, threaded mail,
  immutable submissions, evidence and provider-operation status.
- The detail page provides cancellation and final Markdown report download.

Source/model text is escaped in the interface. Reading the operator page does
not mark an agent's assigned task complete. Refresh the detail page to inspect
persisted progress; batch inference can remain queued or processing without new
mail until its response arrives.

## API

The API uses the existing `Authorization: Bearer <application-api-key>`
authentication. This is an application key created through the API-key admin,
not the OpenRouter provider key. All endpoints require authentication.

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
    "max_turns": 20,
    "max_cost_usd": "10.00",
    "max_duration_s": 86400,
    "finder_model": "deepseek/deepseek-v4.1-flash:batch",
    "validator_model": "deepseek/deepseek-v4.1-flash:batch",
    "connectors": ["duckduckgo", "web_fetch"]
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
| `GET /api/v1/connectors/` | Capability and safe configuration status |

Paginated responses contain `count`, `limit`, `offset`, `next_offset` and
`results`. Limits are 1–200. An omitted limit defaults to 50. Report downloads
are Markdown text rather than executable HTML. Cancellation prevents new calls;
work already submitted may still incur a charge.

## Recovery and billing

Messages, assignments, completed turns and exact proposal references are
persisted. Reading mail is not task completion. Transactional dispatch records
allow committed tasks to be published after temporary broker failures. Leases
and fencing prevent a stale worker from committing another handoff.

External operation records distinguish planned, submitted, completed and unknown
outcomes. Poll an already-submitted batch using its stored provider identity;
do not create another job just because the worker restarted. Unknown billed
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

This uses real PostgreSQL, an extension-registry fixture connector and
`httpx.MockTransport` for all OpenRouter traffic. It demonstrates a four-turn
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
