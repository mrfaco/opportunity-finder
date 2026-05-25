# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

`AGENTS.md` is the rulebook for any AI agent touching this repo. It covers exception discipline, the test/coverage ratchet, prompts-in-git, migration sync, module boundaries, and more — all enforced by pre-commit/pre-push hooks. Don't restate or override it here; defer to it.

## Architecture in one paragraph

This is a **workflow with one agentic step**. Ingestion adapters (`ingestion/adapters/`) emit `IngestedItem`s, which a Haiku-based filter (`ingestion/filter.py`) classifies, which the online clustering algorithm (`clusters/clustering.py`) assigns to a nearest-centroid cluster or spawns a new singleton. A nightly refinement pass queues merge/split proposals for human approval in Django admin. The only true *agent* is the investigation loop (`agents/loop.py` + `agents/orchestrator.py`) that reads a cluster and produces a structured brief via tool calls. Celery Beat schedules ingestion + refinement; the investigation queue runs on demand from admin. **Prompts live in `prompts/` (git), never in the DB.** The DB stores prompt hashes for trajectory replay, not content.

Module boundaries that matter (also in AGENTS.md §10):
- Only `agents/orchestrator.py` and `agents/loop.py` write `AgentRun`/`AgentStep`/`AgentEvent` rows.
- Adapters emit `IngestedItem`s — they do not classify or cluster.
- Anything deterministic stays a function or Celery task. The investigation loop is the only agent.

## Common commands

Everything runs through Docker Compose via the Makefile — **don't call `docker compose` directly without `HOST_UID`/`HOST_GID` exported**, or the container writes root-owned files into the bind-mounted repo. The Makefile exports them automatically.

```sh
make up                  # start db (pgvector) + redis + web + celery worker + beat
make migrate             # apply migrations
make makemigrations      # generate migrations after model edits
make test                # pytest -v (inside web container, real Postgres + pgvector)
make coverage            # pytest with coverage report; gate is in pyproject.toml
make coverage-ratchet    # raise the gate to today's floor (never lowers)
make lint                # ruff check (host venv)
make format              # ruff format + ruff check --fix
make typecheck           # mypy . (inside container)
make discipline          # custom exception-discipline checker
make validate-prompts    # YAML frontmatter + body check for prompts/**/*.md
make check-migrations    # makemigrations --check --dry-run
make shell               # Django shell in the web container
make hooks-install       # one-time: install pre-commit + pre-push git hooks
make hooks-run           # run all hooks against every file
```

Run a single test:

```sh
docker compose run --rm web pytest tests/test_clustering.py::test_centroid_update -v
```

Trigger ingestion manually from `make shell`:

```python
from ingestion.tasks import ingest_source
ingest_source.delay("hacker_news")
```

## Layout

| Concern | Location |
|---|---|
| Django project (settings, celery, urls) | `config/` |
| Prompts (source of truth, hashed by content) | `prompts/` — see `prompts/README.md` |
| Cluster models, online assignment, nightly refinement | `clusters/` |
| Source adapters, Haiku filter, ingestion pipeline | `ingestion/` |
| Agent loop, orchestrator, tools, prompt loader, cost tracking | `agents/` |
| Investigation review queue + brief models | `investigations/` |
| Approver UI + daily digest | `notifications/` |
| Pydantic schemas, context helpers shared across modules | `core/` |
| Custom checkers + coverage ratchet | `scripts/` |
| Docker entrypoint config | `Dockerfile`, `docker-compose.yml` |

Admin URLs worth knowing (see README for the full list): `/admin/agents/cost-dashboard/`, `/admin/agents/agentrun/<id>/trajectory/`, `/admin/ingestion/filter-labeling/`, `/admin/clusters/clustermergeproposal/`, `/admin/investigations/investigation/`.

## Gotchas

- **Container UID.** If you ever bypass the Makefile, prefix with `HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose ...`. Otherwise `migrations/`, `coverage.xml`, `.mypy_cache/`, etc. end up root-owned and you'll fight permission errors.
- **Imports are absolute and top-of-file** (`TID252`, `PLC0415`). A deliberately deferred import needs `# noqa: PLC0415` with a one-line reason. See AGENTS.md §4.
- **Editing a prompt = a commit.** No admin UI. The DB only records which prompt hash a run used.
- **Don't lower `--cov-fail-under` by hand.** Use `make coverage-ratchet`; it only raises.
- **Schema versions.** Every structured artifact (briefs, tool I/O, prompt frontmatter) carries a `schema_version`. When the shape changes, bump it in the same commit as the migration path.
