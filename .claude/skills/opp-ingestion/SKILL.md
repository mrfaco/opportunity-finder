---
name: opp-ingestion
description: |
  Trigger and inspect ingestion runs against the opportunity-finder HTTP API.
  Use when the user wants to start an incremental ingest, run a backfill,
  check what was recently ingested, or look at task-run history. Does not
  call the database directly — everything goes through /api/v1/ingestion/*
  with API-key auth.
---

# opp-ingestion — operate the ingestion layer via API

You drive the opportunity-finder ingestion pipeline through its HTTP API.
This skill exposes four operations: trigger incremental ingest, trigger
backfill, list recent items, list task-run history.

## Environment

Both env vars are **required**. Fail fast with a clear message if either
is unset — do not guess defaults.

```sh
OPP_API_BASE   # e.g. http://localhost:8000 (no trailing slash)
OPP_API_KEY    # opp_<32 chars>. Created in /admin/api/apikey/create-key/
```

Check before doing anything:

```sh
[ -z "$OPP_API_BASE" ] && { echo "OPP_API_BASE not set"; exit 1; }
[ -z "$OPP_API_KEY" ]  && { echo "OPP_API_KEY not set";  exit 1; }
```

## Operations

| Operation | Method + path | Required body / query |
|---|---|---|
| Trigger ingest | POST `/api/v1/ingestion/runs/` | `{"source": "hacker_news"\|"github"}` |
| Trigger backfill | POST `/api/v1/ingestion/backfills/` | `{"source": "...", "days": <int 1-365>}` |
| List task runs | GET `/api/v1/task-runs/?task_prefix=ingestion.tasks.&args_contains=<src>&limit=N` | generalized task history; pass the ingestion prefix to scope |
| List recent items | GET `/api/v1/ingestion/items/?source=<name>&limit=N` | optional filters |
| List checkpoints | GET `/api/v1/ingestion/checkpoints/` | — |

All responses are JSON. Auth via `Authorization: Bearer $OPP_API_KEY`.

### Curl templates

Trigger incremental ingest:

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/ingestion/runs/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"source": "hacker_news"}'
# → 202 {"task_id":"...","status":"queued"}
```

Trigger backfill (N days):

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/ingestion/backfills/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"source": "github", "days": 7}'
```

List recent task runs (defaults to 50). The `/task-runs/` endpoint is
shared across all Celery tasks — scope to ingestion via `task_prefix`:

```sh
curl -sS "$OPP_API_BASE/api/v1/task-runs/?task_prefix=ingestion.tasks.&limit=10" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {task_name, status, date_done, traceback}'
```

Add `args_contains=hacker_news` to filter to one source.

List recent ingested items:

```sh
curl -sS "$OPP_API_BASE/api/v1/ingestion/items/?source=hacker_news&limit=20" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {title, source_item_id, cluster_id, classifier_verdict}'
```

## Behavior rules

- **Preview before mutating.** For `POST` endpoints, show the user the
  exact curl + body before sending. Wait for confirmation.
- **GET is free.** No confirmation needed for read-only endpoints.
- **Don't dump full JSON.** Use `jq` to extract the bits worth showing.
  Full payload only on explicit request.
- **Surface failure loudly.** Non-2xx responses → print status + body and
  stop. Do not retry silently.
- **Idempotency.** Triggering an ingest twice in a row enqueues two task
  runs — this is intentional (the second is a no-op if the first hasn't
  advanced the checkpoint yet). Confirm with the user before re-triggering
  within the same minute.

## Status codes worth recognizing

- `202 Accepted` — task queued. Returned task_id is a Celery id; you can
  look it up in the task-runs endpoint a few seconds later.
- `400 Bad Request` — invalid source name or invalid `days`. Body has the
  field-level error.
- `401 Unauthorized` — missing/bad/revoked API key. Re-create one in
  admin if needed.
- `500` — usually broker unreachable. Don't retry; tell the user the
  Celery worker may be down.
