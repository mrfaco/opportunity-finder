# Quickstart

Get the server running locally and smoke-test the pipeline end-to-end.

## Prerequisites

- Docker + Docker Compose
- `make` (used to set `HOST_UID`/`HOST_GID` so bind-mounted files stay host-owned — see [docker-compose.yml](docker-compose.yml))
- An `ANTHROPIC_API_KEY` (classifier + investigation agent)
- A `VOYAGE_API_KEY` (embeddings — get one at https://www.voyageai.com/)

## 1. Configure

```sh
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
DJANGO_SECRET_KEY=<any-random-string>
```

The rest of `.env.example` ships with working defaults for local dev.

## 2. Build and migrate

```sh
make build
make migrate
make createsuperuser
```

`make migrate` runs against the dockerized Postgres (with pgvector). `createsuperuser` gives you an admin login.

## 3. Start the stack

```sh
make up
```

This brings up four services:

| Service          | Purpose                                          |
| ---------------- | ------------------------------------------------ |
| `db`             | Postgres 16 + pgvector                           |
| `redis`          | Celery broker / result backend                   |
| `web`            | Django dev server on http://localhost:8000       |
| `celery_worker`  | Executes ingestion + investigation tasks         |
| `celery_beat`    | Cron scheduler (hourly ingest, nightly refine)   |

Use `make logs` to tail everything, `make down` to stop, `make ps` to inspect.

Then open http://localhost:8000/admin/ and log in.

## 4. Seed the Celery Beat schedule

Once-per-database setup that registers the recurring jobs (hourly HN ingest, nightly cluster refinement):

```sh
docker compose run --rm web python manage.py setup_schedules
```

Confirm in `/admin/django_celery_beat/periodictask/` that the rows appear.

## 5. Smoke-test end-to-end

Each step costs more than the previous — run them in order so a cheap failure catches a wiring issue before you spend more.

### 5a. Filter eval (~$0.05–0.10)

Loads the 200-item eval set and runs every row through the live Haiku classifier. Catches API key issues, prompt loading, structured-output parsing, cost math.

```sh
docker compose run --rm web python manage.py load_eval_set
make shell
# in the shell:
from ingestion.tasks import run_filter_eval
run_filter_eval()
```

Check `/admin/ingestion/filter-eval-history/` for precision / recall / F1 by tier.

### 5b. HN ingestion (~$0.10–0.30)

Pulls the last 7 days of Ask HN posts, classifies each, embeds the keepers, assigns them to clusters.

```sh
make shell
# in the shell:
from ingestion.tasks import ingest_source
ingest_source("hacker_news")
```

Inspect:

- `/admin/ingestion/filterclassification/` — every classified item
- `/admin/clusters/cluster/` — clusters with items attached

### 5c. Investigation run (~$0.30–0.80)

Run the agent loop against a real cluster.

```sh
make shell
# in the shell:
from clusters.models import Cluster
from agents.orchestrator import start_run

# pick the largest cluster
cluster = Cluster.objects.order_by("-size").first()
run = start_run(cluster.id, "investigation")
print(run.id)
```

Then watch:

- `/admin/agents/agentrun/<run.id>/trajectory/` — step-by-step model + tool calls
- `/admin/investigations/investigation/` — the recorded brief once the agent terminates

If you want to skip the Celery worker and run inline (easier to debug):

```python
from agents.loop import run_loop
run_loop(run.id)
```

## Useful admin pages

- `/admin/` — index of everything
- `/admin/agents/cost-dashboard/` — daily spend, per-model breakdown, top runs
- `/admin/agents/prompts/` — current prompt hashes + history
- `/admin/clusters/clustermergeproposal/` and `.../clustersplitproposal/` — refinement queues awaiting approval

## Troubleshooting

| Symptom                                                          | Fix                                                                                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Files written by docker are root-owned                           | Use `make` instead of `docker compose` directly. Direct calls need `HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose ...`.         |
| `psycopg.OperationalError: could not connect to server`          | `make down && make up` — the web container starts before Postgres is healthy on cold boots; the healthcheck retries usually win.     |
| Classifier returns 401                                           | `ANTHROPIC_API_KEY` missing or wrong in `.env`. Restart the stack after editing — env vars aren't hot-reloaded.                      |
| Embedding fails with dimension mismatch                          | You probably changed `EMBEDDING_MODEL`. Run `python manage.py reembed_cluster_items` to rebuild centroids at the new dimension.      |
| Periodic tasks don't fire                                        | `celery_beat` only schedules tasks that `setup_schedules` has registered. Re-run it.                                                 |
