# Pain-Mining Opportunity Agent

An open-source agentic system that ingests public discussions from Hacker News, GitHub,
Stack Overflow, and Product Hunt, identifies clusters of related complaints and unmet
needs, and uses an LLM-powered investigation agent to produce structured opportunity
briefs for human review.

## Architecture

The system is a **workflow with one agentic step**, following Anthropic's
"Building Effective Agents" guidance — use the simplest pattern that works, only
introduce autonomy where it earns its complexity:

```
[ingestion firehose] → [Haiku classifier filter — deterministic] →
[online clustering — deterministic] → [investigation agent — agentic loop] →
[brief produced] → [Django admin human review]
```

The first three stages are workflows (predefined code paths with LLM calls).
Only the investigation step is a true agent — it has autonomy over tool selection
and termination because investigating a cluster genuinely requires open-ended
exploration.

**Prompts live in git, not in the database.** The `prompts/` directory is the
source of truth. The database tracks which prompt hash was used in which run,
but never stores or edits prompt content. To change a prompt, edit the file,
commit, deploy.

**Tools follow an MCP-compatible shape.** Each tool has a `name`, `description`,
JSON-Schema `input_schema`/`output_schema`. For v1 they are internal Python
functions; in v2 we can expose them through an MCP server with no change to
the agent loop.

**Clustering is the substrate everything else stands on.** Online assignment
(nearest centroid above threshold or new singleton) happens at ingestion. A
nightly refinement task recomputes centroids, finds orphans, and queues merge
and split proposals for human approval through Django admin.

## Quickstart

```sh
cp .env.example .env
docker-compose build
docker-compose up
make migrate
make createsuperuser
docker-compose run --rm web python manage.py setup_schedules   # seed Celery Beat jobs
```

To run ingestion you also need `ANTHROPIC_API_KEY` (classifier) and
`VOYAGE_API_KEY` (embeddings) in `.env`. Once `setup_schedules` has run,
the `celery_beat` service ingests Hacker News hourly; trigger it manually
with `ingestion.tasks.ingest_source.delay("hacker_news")` from `make shell`.

Then navigate to `http://localhost:8000/admin/`.

## Where to find each major component

| Concern | Location |
|---|---|
| Django project | `config/` |
| Prompt files (source of truth) | `prompts/` |
| Cluster models + online/refinement algorithms | `clusters/` |
| Ingestion adapters + Haiku filter | `ingestion/` |
| Agent loop, orchestrator, tools, prompt loader | `agents/` |
| Investigation review queue | `investigations/` |
| Approver interface + daily digest | `notifications/` |
| Pydantic schemas + context helpers | `core/` |

### Admin pages worth knowing

- `/admin/` — index of all models
- `/admin/agents/cost-dashboard/` — daily spend, per-model, top expensive runs
- `/admin/agents/prompts/` — read-only prompt inspector (current hashes + history)
- `/admin/agents/agentrun/<id>/trajectory/` — step-by-step trajectory viewer
- `/admin/ingestion/filter-labeling/` — keyboard-driven labeling UI for the eval set
- `/admin/ingestion/filter-eval-history/` — eval run history, per-tier breakdown
- `/admin/clusters/clustermergeproposal/` — pending merges awaiting human approval
- `/admin/clusters/clustersplitproposal/` — pending splits awaiting human approval
- `/admin/investigations/investigation/` — triage queue of generated briefs

## Status

**v1 (this commit):** Runnable skeleton. All data models, abstractions, admin
surfaces, and orchestration scaffolding in place. The clustering control flow
is complete and uses mock random embeddings. The agent loop control flow is
complete with stubbed LLM and tool implementations. Prompts are TODO stubs.

**Deferred (see `NEXT_STEPS.md`):**

1. Author the filter classifier prompt and build the seed eval set
2. Plug in a real embedding model
3. Implement the Hacker News ingestion adapter
4. Implement `query_cluster` and other tools
5. Author the investigation system + procedural prompts
6. Plumb real Anthropic API calls into the filter and investigation agent
7. Implement the LLM judge for merge/split proposals
8. Implement remaining adapters and tools
9. Polish the trajectory viewer
10. Cross-run caching for `fetch_url`, `fetch_hn_item`, `query_trustmrr`

## Design

See `docs/DESIGN.md` (placeholder — to be authored alongside the prompt
authoring session).

## Contributing

This is a public, MIT-licensed repository. It is deliberately AI-coded —
the rules and hooks below exist so review burden stays minimal.

**Read [`AGENTS.md`](AGENTS.md) first.** It documents the discipline this
codebase holds itself to: never swallow exceptions, never silently fall
back, every change has a test, coverage only ratchets up.

One-time setup after cloning:

```sh
make hooks-install   # installs pre-commit + pre-push git hooks
```

The hooks do the rest:

- **pre-commit (fast):** `ruff format`, `ruff check`, the custom
  exception-discipline checker, plus standard whitespace / large-file
  guards.
- **pre-push (slow):** full `pytest` against Postgres + Redis with the
  coverage gate from `pyproject.toml`.

Useful commands:

```sh
make test                # run the suite
make coverage            # run with coverage report
make coverage-ratchet    # bump the coverage gate to today's floor
make discipline          # just the exception-discipline check
make hooks-run           # run all configured hooks against every file
```

## License

MIT — see `LICENSE`.
