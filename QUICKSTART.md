# Local quickstart

The primary workflow is a search-led dialogue between Finder and Validator.
You need Docker Compose, `make`, and a funded OpenRouter API key. General web
search also needs a configured connector. Historical ingestion and clustering
are optional and have separate credentials.

## Configure

If `.env` does not already exist, copy `.env.example` to `.env`. Preserve any
existing keys. Add the following entries without committing their values:

```dotenv
OPENROUTER_API_KEY=<your-key>
DJANGO_SECRET_KEY=<a-random-development-secret>
TAVILY_API_KEY=<your-tavily-key>
```

Check `.env.example` for Discovery defaults. Both agent models default to
`deepseek/deepseek-v4.1-flash:batch`. The application submits asynchronous batch
inference through OpenRouter; jobs may wait hours. Choose a duration that permits
that wait. There is no automatic switch to another model.

The standard `web_search` connector uses Tavily; `web_fetch` retrieves actual
pages. Additional explicitly configured connectors can supplement research.
`GET /api/v1/connectors/` and the admin start page show configuration status.
Do not select a connector whose required credentials are absent.

## Build and start

```sh
make build
make migrate
make createsuperuser
make up
```

The Makefile sets host UID/GID so container writes remain owned by you. Services
include PostgreSQL with pgvector, Redis, Django on port 8000, a Celery worker and
Celery Beat. No production deployment is needed.

Open http://localhost:8000/admin/discovery/discovery/ and log in. Start a
Discovery with an objective such as:

> Look for opportunities in travel for independent operators in Argentina.
> Consider services, physical products and information businesses as well as software.

Set capital/geography/skills constraints if relevant. The default 20 turns means
10 Finder–Validator exchanges. Both roles have their own research calls and
model-call limits inside each turn; these are not additional dialogue turns.
Set an appropriate cost ceiling and wall-clock duration before starting.

## Inspect the run

The detail page shows status, known spend, persisted mail, immutable turn
submissions, captured evidence and external-operation status. Batch work can
remain queued or processing; refresh the page to inspect current state.

Validator can challenge a crowded market and suggest a narrower niche. Finder
must adopt and submit a revision before Validator can accept it. Acceptance of
that exact revision can end the run early. A turn limit with unresolved
objections is reported honestly; a promising unreviewed idea is not convergence.

Download the final Markdown report. It includes the business model, evidence
for and against, pivots, unresolved assumptions and next human tests. Cancel a
running discovery from its detail page. Cancellation stops new calls but cannot
undo a provider charge or guarantee cancellation of work already submitted.

## Optional historical ingestion

Discovery does not require `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, a seed eval set,
or source ingestion. Those belong to the historical classifier/embedding and
investigation workflow. Existing schedules remain enabled unless an operator
changes them.

To use that separate workflow, configure its keys and deliberately run
`setup_schedules` inside the configured web container. Inspect periodic tasks at
`/admin/django_celery_beat/periodictask/` before enabling recurring paid work.

## Development verification

Prototype work uses targeted smoke checks first. Extensive failure injection,
regression tests and coverage verification are scheduled after the end-to-end
prototype works. See [the implementation plan](docs/superpowers/plans/2026-11-03-finder-validator-plan.md).

For API examples, connector development and interrupted-run recovery, see
[docs/discovery.md](docs/discovery.md).
