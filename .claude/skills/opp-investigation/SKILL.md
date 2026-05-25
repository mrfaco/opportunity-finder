---
name: opp-investigation
description: |
  Queue investigations against clusters, list clusters worth investigating,
  list/inspect existing investigations and their briefs via the opportunity-
  finder HTTP API. Use when the user wants to pick a cluster, kick off an
  investigation, or read the brief from a finished one. Promotion / rejection
  / stale-marking is handled by the opp-promotion skill, not this one.
---

# opp-investigation — pick clusters, queue runs, read briefs

This skill is the read + queue side of the investigation lifecycle.
State-changing decisions (promote / reject / mark stale) live in the
opp-promotion skill.

## Environment

Same two env vars as the other opp-* skills:

```sh
OPP_API_BASE   # e.g. http://localhost:8000
OPP_API_KEY    # opp_<32 chars>
```

Fail fast if either is unset.

## Operations

| Operation | Method + path | Notes |
|---|---|---|
| List candidate clusters | GET `/api/v1/clusters/?min_size=<N>&has_pending=false&limit=<N>` | `has_pending=false` ≈ "not yet investigated" |
| Queue investigation | POST `/api/v1/investigations/runs/` | body: `{"cluster_id": "<uuid>"}` |
| List investigations | GET `/api/v1/investigations/?status=<status>&limit=<N>` | filterable by status |
| Get one (full brief) | GET `/api/v1/investigations/<uuid>/` | returns brief + cluster_snapshot |

`status` values: `draft`, `awaiting_review`, `promoted`, `rejected`,
`stale`, `superseded`.

### Curl templates

Find clusters worth investigating (size ≥ 3, no pending investigation):

```sh
curl -sS "$OPP_API_BASE/api/v1/clusters/?min_size=3&has_pending=false&limit=10" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {id, title, size, classifier_score, last_seen_at}'
```

Queue an investigation for a specific cluster:

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/investigations/runs/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"cluster_id\": \"$CLUSTER_ID\"}"
# → 202 {"run_id":"...","cluster_id":"...","status":"queued"}
```

List investigations awaiting human review:

```sh
curl -sS "$OPP_API_BASE/api/v1/investigations/?status=awaiting_review&limit=20" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {id, headline, confidence, created_at}'
```

Read one investigation's full brief:

```sh
curl -sS "$OPP_API_BASE/api/v1/investigations/$INV_ID/" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.brief'
```

## Behavior rules

- **Preview before queueing.** `POST /investigations/runs/` costs money
  (each investigation burns LLM budget). Show the cluster you're about to
  investigate — its title, size, and last_seen_at — and confirm before
  sending.
- **Show the headline first.** When listing investigations, default to
  showing `headline`, `confidence`, `status`, `created_at`. Only fetch
  the full brief on explicit request.
- **Don't paginate by hand.** `limit` caps at 200 server-side. If the
  user wants "all" of something, tell them to increase `limit` rather
  than looping.
- **Investigation id ≠ run id.** A queued investigation returns a
  `run_id` (AgentRun). The `Investigation` row materializes only after
  the agent loop finishes and produces a brief — minutes later. Don't
  assume the investigation id is available right after queueing.

## Status codes

- `202` — queued. Run id is a UUID; investigation will appear in the list
  endpoint once the loop finishes.
- `400` — invalid body (missing cluster_id, malformed UUID).
- `404` — unknown cluster id.
- `409` — cluster is in `merged_into` or `split` status; not investigable.
