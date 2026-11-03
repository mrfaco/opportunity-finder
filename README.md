# Opportunity Finder

Search-led business opportunity research with two agents: **Finder** proposes an
opportunity and **Validator** tries to falsify it. Their durable internal mail
preserves objections, revisions and pivots until they agree or reach the turn
limit. The final report explains what survived scrutiny, what remains disputed,
and which assumptions a human should test next.

Opportunities can be services, manufacturing, intelligence, physical products,
software, B2B, B2C or another model. The agents do not default to SaaS.

## Discovery workflow

```text
Human objective → Finder research + proposal → Validator independent research
                         ↑                          │
                         └── objections / pivot ────┘
                                       ↓
                          agreement or turn/budget limit
                                       ↓
                            cited human-readable report
```

A discovery starts from an objective such as “Look for opportunities in the
travel space.” It requires no ingested data, cluster or embeddings. Default:
100 individual turns, meaning fifty Finder–Validator exchanges, with configurable
research, cost and duration limits. Agreement can finish earlier; it applies to
an exact proposal revision and must pass evidence checks. Model agreement is
not proof of commercial viability.

Both roles run through Claude Code, using the same harness pattern as Video
Swarm. Select **Ollama, OpenRouter or Anthropic** independently for each role.
New discoveries default to OpenRouter's interactive
`deepseek/deepseek-v4.1-flash`. Existing batch records retain their original
configuration. Claude Code discoveries reject `:batch` model IDs explicitly.
The runtime never silently changes the selected provider or model.

Research connectors expose search/fetch capabilities independently of ingestion:
Tavily general web search, bounded web page retrieval, Hacker News, GitHub,
Stack Exchange, and stored local records. Additional connectors implement the
same interface. See [the discovery guide](docs/discovery.md) for configuration,
connector availability, API usage and recovery.

## Run locally

Follow [QUICKSTART.md](QUICKSTART.md). Keep credentials in the ignored `.env`.
Discovery needs Claude Code, a configured selected provider, and a general
web-search connector. Ollama selection additionally needs its launcher.
The historical ingestion workflow separately requires its classifier and
embedding credentials.

```sh
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8088
```

Run the host Celery worker and Beat in separate terminals as described in the
quickstart. The original Python-only Docker image does not supply Claude Code
or Ollama; explicitly provision those clients before using container workers.

Open `/admin/discovery/discovery/` to start a run, inspect mail, evidence and
provider status, or download a completed Markdown report. The authenticated
API lives under `/api/v1/discoveries/`; `/api/v1/connectors/` describes available
research capabilities without returning secret values.

## Layout

| Concern | Location |
|---|---|
| Finder–Validator workflow, durable mail and reports | `discovery/` |
| Read-only search/data connectors | `discovery/connectors/` |
| Git-managed agent prompts | `prompts/finder/`, `prompts/validator/` |
| Django project, Celery and configuration | `config/` |
| Authenticated operator API | `api/`, `discovery/api.py` |
| Historical ingestion, classification and clustering | `ingestion/`, `clusters/` |
| Historical investigation/ideation workflow | `agents/`, `investigations/`, `ideation/` |

## Legacy ingestion workflow

The earlier pipeline remains available:
public-source ingestion → classifier → vector clustering → investigation →
human review → ideation. Existing records and schedules are retained. New
search-led discoveries do not depend on that pipeline. Its admin tools remain
accessible for historical data and optional local-corpus research.

Do not run `setup_schedules` merely to enable Discovery: existing ingestion jobs
are a separate opt-in workflow. Review enabled periodic tasks before using
provider credentials; migrations do not disable existing schedules.

## Development

Read [AGENTS.md](AGENTS.md) before changing code. Prompts and versioned contracts
live in git, failures remain visible, and state changes belong to orchestration.
The current feature prioritizes a working prototype with focused smoke checks;
extensive tests and coverage work follow in the final hardening phase, as
requested by the user. The committed coverage threshold is not lowered.

```sh
make lint
make discipline
make validate-prompts
make check-migrations
make test
```

Design: [specification](docs/superpowers/specs/2026-11-03-finder-validator-design.md)
and [implementation plan](docs/superpowers/plans/2026-11-03-finder-validator-plan.md).

MIT licensed; see [LICENSE](LICENSE).
