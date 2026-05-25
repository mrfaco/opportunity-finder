---
name: opp-promotion
description: |
  Promote, reject, or mark-stale an investigation in opportunity-finder via
  HTTP API. Use when the user has reviewed an investigation brief and wants
  to act on it — promoting flips status to "promoted" and enqueues an
  ideation agent run. Also exposes ideation read endpoints to inspect what
  came out of past promotions.
---

# opp-promotion — act on reviewed investigations

State-changing decisions on investigations live here. Read-only browsing
is in the opp-investigation skill.

## Environment

```sh
OPP_API_BASE   # e.g. http://localhost:8000
OPP_API_KEY    # opp_<32 chars>
```

## Operations

| Operation | Method + path | Body |
|---|---|---|
| Promote (→ enqueues ideation) | POST `/api/v1/investigations/<uuid>/promote/` | (empty) |
| Reject | POST `/api/v1/investigations/<uuid>/reject/` | `{"reason": "..."}` (optional) |
| Mark stale | POST `/api/v1/investigations/<uuid>/stale/` | `{"stale_reason": "..."}` (default `manual`) |
| List ideations | GET `/api/v1/ideations/?status=<status>&limit=<N>` | — |
| Get one ideation | GET `/api/v1/ideations/<uuid>/` | — |
| Download ideation as PDF | GET `/api/v1/ideations/<uuid>/pdf/` | application/pdf — print-styled three-concept render |

Valid `stale_reason` values: `prompt_changed`, `cluster_changed`, `age`, `manual`.

Valid ideation `status` values: `draft`, `awaiting_review`, `accepted`, `rejected`, `stale`.

### Curl templates

Promote an investigation (enqueues an ideation run as a side-effect):

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/investigations/$INV_ID/promote/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json"
# → 200 {"investigation":{...}, "ideation_id":"...", "ideation_run_id":"..."}
```

Reject with a reason:

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/investigations/$INV_ID/reject/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "duplicate of INV-123"}'
```

Mark stale (e.g. because the underlying cluster changed):

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/investigations/$INV_ID/stale/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"stale_reason": "cluster_changed"}'
```

Check the ideations that came out of past promotions:

```sh
curl -sS "$OPP_API_BASE/api/v1/ideations/?status=awaiting_review&limit=20" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {id, investigation_id, status, created_at}'
```

Read one ideation's full output:

```sh
curl -sS "$OPP_API_BASE/api/v1/ideations/$IDEATION_ID/" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.output'
```

Download an ideation's concept set as a PDF:

```sh
curl -sS "$OPP_API_BASE/api/v1/ideations/$IDEATION_ID/pdf/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -o "ideation-$IDEATION_ID.pdf"
```

## Behavior rules

- **Always preview the brief first.** Before calling `/promote/`,
  retrieve the investigation via opp-investigation and show the user:
  - headline
  - confidence
  - problem_statement (first 200 chars)
  - recommended_next_step
  Then confirm the promotion explicitly.
- **Promote is expensive.** Each promotion enqueues an ideation agent
  run which burns LLM budget. Never promote without confirmation, and
  never batch-promote without listing each row first.
- **Reject reason is optional but encouraged.** Free-form text; the
  human-decision record is the only place to capture *why*.
- **Stale ≠ rejected.** Stale means the brief is no longer trustworthy
  (cluster shifted, prompt changed, age). Rejected means a human decided
  the opportunity itself doesn't merit pursuing.

## Status codes

- `200` — flip succeeded. Response body is the updated investigation
  (plus ideation ids on promote).
- `400` — invalid `stale_reason` or malformed body.
- `404` — unknown investigation id.
- `409 Conflict` — investigation is not in `awaiting_review`. Body
  includes `current_status` and `expected_status`. Most common cause:
  someone already promoted/rejected it.
