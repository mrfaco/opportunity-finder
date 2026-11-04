# Local quickstart

The primary workflow is a search-led dialogue between Finder and Validator,
followed by Judge review of agreement. All agents run in Claude Code, following
Video Swarm's harness pattern. Choose
Ollama, OpenRouter or Anthropic separately for each role; all three use the same
durable mail, research connectors and final-report workflow.

## Configure the host

Install Claude Code and make `claude` available to the Celery worker. Selecting
Ollama also requires its `ollama` launcher and a configured model/service. For
Anthropic use the host's Claude Code login or `ANTHROPIC_API_KEY`; for OpenRouter
set `OPENROUTER_API_KEY` in the ignored `.env`. Do not paste provider credentials
into the discovery form. The original Python-only Docker image does not install
these agent runtimes; use host processes for this prototype.

If `.env` does not exist, copy `.env.example`; preserve existing credentials.
Configure PostgreSQL with pgvector, Redis and a random Django development secret.
General web search can use `DISCOVERY_CONNECTORS=duckduckgo,web_fetch` without
another API key, or `web_search,web_fetch` with `TAVILY_API_KEY`.

Default execution settings are:

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

Claude Code uses an interactive model to research and exchange mail. Do not use
`:batch` model IDs for new runs: they are rejected instead of silently converted.
Previously created schema-1.0 batch discoveries retain their historical path.
Judge defaults to Kimi K3 in Ollama. A Judge no-go sends the agents to find a new
opportunity; all three roles share the same turn, time and spending limits.

## Start host processes

Install the Python environment and migrate the configured database:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
```

Run these in separate terminals:

```sh
.venv/bin/python manage.py runserver 127.0.0.1:8088
.venv/bin/celery -A config worker --pool=solo --concurrency=1 -l info
.venv/bin/celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

This checkout's local PostgreSQL/Redis ports are documented in
[the discovery guide](docs/discovery.md). Host `.env` addresses differ from Docker
Compose service addresses. Preserve other projects' services and snapshots.

Open http://127.0.0.1:8088/admin/discovery/discovery/ and log in. Start a Discovery:

> Look for opportunities in travel for independent operators in Argentina.
> Consider services, physical products and intelligence businesses as well as software.

Choose each role's provider and its matching model ID. OpenRouter uses
`provider/model`, Anthropic uses `sonnet` or a native Claude ID, and Ollama uses
its exact model tag. Add geography, skills or capital constraints if useful.
The default 20 turns means ten Finder–Validator exchanges; research/model calls
inside a turn have separate limits. Set cost and duration ceilings before start.
Provider/model selections are frozen with the prompts and enabled connectors.

## Inspect the run

The detail page shows selected providers/models, status, spend including
estimates, durable mail, immutable submissions, captured evidence and operation
status. Refresh while Claude Code works. Validator may challenge a saturated
market and suggest a niche; Finder must adopt and submit it before Validator
can accept that exact revision. A promising unreviewed idea is not convergence.

Read or download the final Markdown report, including evidence for and against,
pivots, unresolved assumptions and next human tests. Stopping a run prevents
further work; inference already sent to a provider may still be charged.

## Historical ingestion and development checks

Discovery needs no Anthropic/Voyage ingestion keys, seed eval set or cluster.
Legacy ingestion/clustering records and schedules remain available; migrations
do not silently enable or disable them. Inspect periodic tasks before enabling
recurring paid work.

Use focused offline checks while completing the prototype. Broad regression
coverage and failure injection remain in the final hardening phase, per the
user's instruction. See [the plan](docs/superpowers/plans/2026-11-03-finder-validator-plan.md)
and [the discovery guide](docs/discovery.md) for the API and connector contract.
