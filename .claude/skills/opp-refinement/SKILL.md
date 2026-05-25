---
name: opp-refinement
description: |
  Run the nightly cluster refinement pass (centroids, orphan reassignment,
  merge proposal generation + LLM judging, title regeneration) on demand,
  and act on the merge proposals it produces — list, inspect, apply, or
  reject. Use when the user wants to clean up clusters, see which clusters
  the system thinks should be merged, or trigger refinement before
  reviewing.
---

# opp-refinement — operate the nightly cluster maintenance + merge review

This skill covers two related workflows: **triggering refinement** (the
nightly task) and **acting on its output** (merge proposals waiting for a
human verdict).

## Environment

```sh
OPP_API_BASE   # e.g. http://localhost:8000
OPP_API_KEY    # opp_<32 chars>
```

Fail fast if either is unset.

## Operations

| Operation | Method + path | Notes |
|---|---|---|
| Trigger refinement | POST `/api/v1/refinement/runs/` | enqueues `clusters.tasks.refine_clusters_nightly`; returns task_id |
| Watch refinement progress | GET `/api/v1/task-runs/?task_prefix=clusters.tasks.&limit=5` | latest task results across the refinement family |
| List merge proposals | GET `/api/v1/cluster-merge-proposals/?status=&judge_verdict=&min_judge_confidence=&limit=` | filter by status / judge verdict / confidence |
| Get one proposal | GET `/api/v1/cluster-merge-proposals/<uuid>/` | full judge reasoning + both clusters' summaries |
| Apply (merge them) | POST `/api/v1/cluster-merge-proposals/<uuid>/apply/` | moves items from cluster_b → cluster_a, marks proposal applied |
| Reject (keep separate) | POST `/api/v1/cluster-merge-proposals/<uuid>/reject/` | body: `{"review_notes": "…"}` (optional) |

Valid `status` values: `pending_review`, `approved`, `rejected`, `applied`, `superseded`.
Valid `judge_verdict` values: `true`, `false`, `null` (null = judge hasn't run yet).

### Curl templates

Trigger a refinement run:

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/refinement/runs/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json"
# → 202 {"task_id":"...","status":"queued"}
```

Watch the most recent refinement task:

```sh
curl -sS "$OPP_API_BASE/api/v1/task-runs/?task_prefix=clusters.tasks.&limit=3" \
  -H "Authorization: Bearer $OPP_API_KEY" | jq '.[] | {task_name, status, date_done}'
```

List the proposals where the judge said "merge them" (the high-leverage
queue for a human reviewer):

```sh
curl -sS "$OPP_API_BASE/api/v1/cluster-merge-proposals/?status=pending_review&judge_verdict=true&limit=20" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  | jq '.[] | {id, judge_conf: .llm_judge_confidence, a: .cluster_a_title, b: .cluster_b_title}'
```

Or scan the obvious-no proposals to bulk-reject them:

```sh
curl -sS "$OPP_API_BASE/api/v1/cluster-merge-proposals/?status=pending_review&judge_verdict=false" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  | jq '.[] | {id, conf: .llm_judge_confidence, a: .cluster_a_title, b: .cluster_b_title}'
```

Inspect one proposal in detail (to see the judge's reasoning):

```sh
curl -sS "$OPP_API_BASE/api/v1/cluster-merge-proposals/$PROPOSAL_ID/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  | jq '{status, judge: .llm_judge_verdict, conf: .llm_judge_confidence, reasoning: .llm_judge_reasoning, a: .cluster_a_title, b: .cluster_b_title}'
```

Apply (merge cluster_b INTO cluster_a):

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/cluster-merge-proposals/$PROPOSAL_ID/apply/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json"
```

Reject with optional notes:

```sh
curl -sS -X POST "$OPP_API_BASE/api/v1/cluster-merge-proposals/$PROPOSAL_ID/reject/" \
  -H "Authorization: Bearer $OPP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"review_notes": "different audiences — judge was right"}'
```

## Behavior rules

- **Refinement is expensive at the LLM step.** Each pending merge candidate
  costs ~$0.001 (Haiku call). Step 5 (titles) costs another ~$0.001 per
  drifted cluster. Running refinement every few minutes is wasteful; the
  natural cadence is once per day. Warn the user if they ask to trigger
  it back-to-back.
- **Apply is destructive.** Items move permanently; the absorbed cluster
  flips to `merged_into` status. Always show the proposal's judge reasoning
  + both cluster titles **before** calling apply. Wait for confirmation.
- **Reject is reversible-ish but loud.** Marking a proposal rejected
  blocks the same pair from being re-proposed (the de-dup check in
  refinement skips PENDING_REVIEW; rejected rows stay around for audit).
  Fine to do in bulk for obvious-no proposals.
- **Trust the judge as a prioritizer, not an oracle.** When the judge
  says "merge" with low confidence (< 0.7), the operator should look.
  When confidence is high (> 0.85), the obvious-yes/obvious-no calls are
  usually right. The reasoning string is the deciding factor in
  borderline cases.
- **Don't bulk-apply.** Each apply changes the cluster graph; bulk-
  applying without reading reasoning often produces stupid merges that
  are awkward to undo (the items get re-clustered on next ingest).

## Status codes

- `202` — refinement queued; task_id is a Celery id.
- `200` — list / detail / apply / reject succeeded.
- `400` — invalid filter value (e.g. unknown `status`).
- `404` — proposal id doesn't exist.
- `409 Conflict` — proposal is not in `pending_review` (already applied,
  already rejected, or superseded). Body includes `current_status` and
  `expected_status` — usually the operator action stalls because someone
  else handled the row first.
