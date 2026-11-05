# Discovery quality measurement (roadmap Phase 0)

Date: 2026-11-05
Status: proposed; nothing implemented.
Implementation plan: `../plans/2026-11-05-discovery-quality-measurement-plan.md`.
Roadmap context: Phase 0 of the Opportunity Finder quality roadmap. Later
phases (evidence substance, new sources, reasoning quality) are judged against
the baseline this phase records.

## 1. Outcome and scope

After this phase an operator can answer three questions from persisted
records, without a model call:

1. Was this discovery's result good? A human records a verdict on each
   terminal discovery, and that verdict is visible next to Judge's.
2. How well evidenced was it? Deterministic metrics computed from the
   proposals, reviews, evidence and research operations already stored.
3. Did a change help? A fixed benchmark set of objectives can be run, scored
   and compared with the previous run, prompt hash by prompt hash.

In scope: an append-only human verdict record with admin and API surfaces; a
pure metrics module; a versioned benchmark objective set; two management
commands (run the benchmark, score discoveries); an "Evidence quality" section
in the report; a recorded baseline.

Out of scope: any change to prompts, acceptance gates, connectors or role
defaults; an LLM grader (shared blind spots with the roles it would grade,
and a second thing to validate); language detection of sources; source-type
classification (Phase 1 adds it and bumps the metrics schema). Phase 0 reports
what the records already contain. Where that is "unknown", unknown is the
baseline.

## 2. Terms

| Term | Meaning |
|---|---|
| Terminal discovery | `status` in `completed`, `failed` or `cancelled` |
| Human verdict | One operator's assessment of a terminal discovery's final candidate |
| Quality metrics | Deterministic numbers derived from persisted records; schema-versioned |
| Benchmark set | A git-managed list of objectives with constraints and a human rubric |
| Benchmark run | One start of every benchmark objective under one tag and one frozen config |
| Quality run | One scoring pass over a set of discoveries, persisted for comparison |

## 3. Human verdict

### Record

New append-only model `DiscoveryVerdict`:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `schema_version` | char | `1.0` |
| `discovery` | FK `Discovery` | `related_name="verdicts"` |
| `proposal` | FK `ProposalRevision`, nullable | The final proposal at the time of the verdict; null when none existed |
| `judgment` | FK `JudgmentRevision`, nullable | The current Judge verdict at the time, if any |
| `verdict` | char | `good`, `weak`, `wrong`, `untested` |
| `notes` | text | Free text; may be empty except for `wrong` |
| `recorded_by` | FK `AUTH_USER_MODEL`, nullable (`SET_NULL`) | The admin user or the API key's owner; null only after that user is deleted |
| `recorded_via` | char | `admin` or `api` |
| `recorded_at` | datetime | |

Verdicts are never edited or deleted. The latest verdict by `recorded_at` is
"the" human verdict for display and metrics; the history stays inspectable.
Keeping the proposal and judgment references means a verdict is tied to what
the reviewer actually saw, so a report regenerated later cannot silently
change what was judged.

### Semantics

| Verdict | Means |
|---|---|
| `good` | The final candidate is worth the human's time to test next; the reviewer would act on it |
| `weak` | Directionally plausible but the evidence or economics do not justify acting yet |
| `wrong` | The final candidate is not viable, or the report contains a material factual error; `notes` must say which |
| `untested` | The reviewer read it but cannot judge without information outside the report |

A verdict can be recorded only on a terminal discovery. A discovery with no
proposal can still receive a verdict (`wrong` or `untested` for a failed run
is useful signal). The verdict judges the opportunity, not the agents'
politeness or the report's formatting.

### Surfaces

- Admin review page (`/admin/discovery/discovery/<id>/review/`): a verdict
  form at the top of the report panel, shown only when the discovery is
  terminal and the user has change permission. Submitting POSTs to a new
  `verdict/` admin URL; the page redirects back and shows the history. The
  current `review_view` rejects non-GET; the new URL owns the POST.
- API: `POST /api/v1/discoveries/<id>/verdict/` with body
  `{"schema_version": "1.0", "verdict": "good", "notes": "..."}`; 409 when the
  discovery is not terminal; 201 with the created record. Same bearer-key
  authentication as the other endpoints. `GET /api/v1/discoveries/<id>/`
  gains `human_verdict` (latest or null) and `verdict_count`; the response
  schema moves from `1.1` to `1.2`.
- Report: the latest human verdict, its date and `recorded_via` appear under
  "Final assessment" when one exists. Reports are regenerated on demand via
  `regenerate_report`; recording a verdict triggers a regeneration so the
  stored Markdown matches.

## 4. Quality metrics

### Module

New `discovery/quality.py` exposing `compute_quality(discovery) ->
QualityMetrics`. Pure read of persisted rows; no network, no model calls, no
writes. `QualityMetrics` is a Pydantic contract with `schema_version = "1.0"`
and `extra="forbid"`. Every metric that cannot be computed is `None` with a
reason in `notes`, never a silent zero.

### Metrics (schema 1.0)

Evidence, computed over the final proposal's cited evidence
(`demand_evidence_ids ∪ competitor_evidence_ids ∪ claim references`):

| Metric | Definition |
|---|---|
| `cited_evidence_count` | Distinct evidence rows cited |
| `cited_origin_count` | Distinct `origin` (or `metadata.original_source_origin`) among cited rows, excluding exact content-hash duplicates |
| `cited_connector_counts` | Map connector → cited rows |
| `snippet_citation_share` | Cited rows with `kind == "search_snippet"` ÷ cited rows |
| `fact_claims_with_quote_rate` | Claims of `kind == "fact"` with at least one non-empty quote ÷ fact claims |
| `claims_with_evidence_rate` | Claims with at least one reference ÷ claims |
| `published_at_known_share` | Cited rows with `published_at` set ÷ cited rows |
| `median_evidence_age_days` | Median of `retrieved_at − published_at` where known; `None` otherwise |

Quote–evidence agreement is already enforced at submission by
`validate_references`, so it is not a metric; the metrics above measure what
the gates do not enforce.

Independence, computed over research operations:

| Metric | Definition |
|---|---|
| `validator_query_novelty_min` | Over every Validator search query, the minimum of `1 − max Jaccard(tokens, any earlier Finder query tokens)`; `None` with no Validator searches |
| `accepting_turn_query_novelty` | Same measure restricted to the turn whose validation was `accept`; `None` without acceptance |
| `validator_new_origin_share` | Origins first captured by Validator ÷ all origins captured |

Tokens are casefolded, split on whitespace and punctuation, with no stop-word
list in 1.0 (a stop-word list is a later schema bump, not a silent change).

Process and cost:

| Metric | Definition |
|---|---|
| `turns_completed`, `proposal_count`, `pivot_count` | From turns and `business_fingerprint` changes between consecutive proposals |
| `turns_to_first_accept` | Turn number of the first `accept` validation; `None` if none |
| `judge_verdict_counts` | Map verdict → count |
| `research_calls`, `research_failure_share` | Completed research operations and those with `status` not `success` |
| `local_denials` | Operations with `request_performed == false` |
| `known_cost_usd`, `estimated_cost_usd`, `unknown_operations` | As the report already distinguishes them |

Outcome: `outcome`, `stop_reason`, `judge_required`, `judge_approved`,
`human_verdict` (latest or `None`), `finder_model`, `validator_model`,
`judge_model`, `same_model_family` (same provider and model for Finder and
Validator), and the three role prompt hashes from `prompt_snapshot`.

### Where metrics appear

- Report: a new "Evidence quality" subsection under "Limitations and
  provenance" listing the evidence and independence metrics in plain
  language, plus the human verdict line. `Report` schema moves from `1.1` to
  `1.2` with a `quality` object (the `QualityMetrics` dump) and
  `human_verdict`. Existing stored `report_json` is not rewritten;
  `regenerate_report` produces the new shape on demand.
- API: `GET /api/v1/discoveries/<id>/` gains `quality` for terminal
  discoveries (null while running), under the same `1.2` bump.
- Admin review page: the same metrics as a small table above the report.

## 5. Benchmark set

### File

`discovery/eval/objectives.json`, schema `1.0`, validated by a Pydantic
contract at load time. Twelve objectives in the first version. Each entry:

```json
{
  "id": "ar-cyber-smb",
  "objective": "Look for opportunities in cybersecurity services for small businesses",
  "constraints": "Argentina; services or intelligence businesses; no venture-scale assumptions",
  "business_model_focus": ["services", "intelligence"],
  "region": "AR",
  "language": "es",
  "rubric": {
    "must_cite": ["a domestic buyer or regulator source", "a competitor price or offer"],
    "red_flags": ["overseas BPO staffed in Argentina presented as domestic demand",
                  "market size from a vendor report as the only demand evidence"]
  }
}
```

Composition rules, checked by a test so the set cannot drift quietly: at
least four objectives whose `business_model_focus` excludes software; at
least four with `language != "en"`; at least three regions; no two
objectives with the same `(region, business_model_focus)` pair. IDs are
stable; retiring an objective means removing it and noting why in
`docs/discovery-eval.md`, never reusing its ID.

The rubric is for the human reviewer in Phase 0: the admin review page shows
it beside the verdict form when the discovery carries a benchmark objective
ID. It is not scored automatically; Phase 1 can automate parts once evidence
has a `source_type`.

### Linking discoveries to the benchmark

`Discovery` gains two blank-by-default fields: `benchmark_tag` (char, indexed)
and `benchmark_objective_id` (char). Only the benchmark command sets them.
They are exposed read-only in the API and admin list filters. This is
preferred over encoding the tag in the idempotency key because a query by tag
must not depend on string parsing.

## 6. Commands

### `manage.py run_discovery_benchmark`

Starts one discovery per benchmark objective through the existing idempotent
start service, with idempotency key `benchmark:<tag>:<objective_id>`, so a
re-run with the same tag returns the same discoveries instead of paying twice.

Arguments: `--tag` (required, `[a-z0-9-]+`), `--objective` (repeatable, to
run a subset), `--config-json` (optional override merged over deployment
defaults and validated as `DiscoveryConfig`), `--max-total-usd` (required
when not a dry run), `--authorize-spend` (literal flag; without it the command
prints what it would start and exits 0).

Behaviour: the command refuses to start if `sum(max_cost_usd)` over the
selected objectives exceeds `--max-total-usd`; it prints the frozen config,
prompt hashes and per-objective idempotency keys; it never starts a second
discovery for a key that already exists. It does not wait for completion;
discoveries run through the normal worker path.

### `manage.py score_discoveries`

Scores a set of discoveries and persists the result.

Arguments: `--tag` or `--discovery` (repeatable); `--since` (ISO date,
optional); `--compare-to <quality_run_id>` (optional); `--format
markdown|json`.

Behaviour: computes `compute_quality` for each terminal discovery selected,
skips non-terminal ones with a visible note, writes one `DiscoveryQualityRun`
row and prints the aggregate. With `--compare-to`, prints per-metric deltas
against the earlier run and flags prompt-hash or config differences between
the two sets so a comparison across different prompts is labelled as such.

New model `DiscoveryQualityRun` (append-only): `id`, `schema_version`,
`tag`, `discovery_ids`, `metrics_schema_version`, `aggregate` (JSON: per-metric
mean, median, min, max, count and `None` count), `per_discovery` (JSON list of
`QualityMetrics` dumps), `prompt_hashes` (JSON, distinct per role),
`config_snapshots_distinct` (int), `created_at`. Aggregates over `None`
values exclude them and report the exclusion count.

## 7. Baseline

The baseline is one benchmark run with current deployment defaults
(`search_auto,web_fetch`, DeepSeek V4.1 Flash for Finder and Validator,
`kimi-k3:cloud` Judge), scored after every discovery is terminal, with a human
verdict on each. It is a human-authorized paid step: twelve objectives at the
default USD 10 ceiling is a USD 120 ceiling, expected lower. The baseline is
recorded in `docs/discovery-eval.md` with the tag, date, prompt hashes, config,
aggregate metrics, verdict distribution and total known and estimated cost.

Every later phase re-runs the same tag family (`baseline-2026-11`,
`phase1-…`) and records its row in the same file with its delta. A metrics
schema bump invalidates numeric comparison for changed metrics; the file says
so for that row.

## 8. Acceptance scenarios

1. An operator opens a completed discovery, records `weak` with a note, and
   the review page, the API detail and the regenerated report all show it;
   a second verdict `good` later shows as latest with both in history.
2. Recording a verdict on a running discovery is refused by admin and API
   with a clear message; nothing is written.
3. `compute_quality` on the existing smoke fixtures (`scripts/smoke_discovery.py`
   data and the PDF/search fixtures) returns a `QualityMetrics` with
   `snippet_citation_share`, `cited_origin_count` and
   `validator_query_novelty_min` populated and `median_evidence_age_days`
   `None` with a note, because fixture evidence has no `published_at`.
4. A Validator whose only search is Finder's query reworded with one token
   changed scores a novelty near zero; a genuinely different query scores
   near one.
5. `run_discovery_benchmark --tag t1` without `--authorize-spend` starts
   nothing and prints twelve planned keys; with authorization and
   `--max-total-usd 50` it refuses because the ceiling sum is 120; with 120
   it starts twelve discoveries; running it again starts none.
6. `score_discoveries --tag t1` on a mix of terminal and running discoveries
   scores the terminal ones, lists the skipped ones, persists one run and
   prints a Markdown table; `--compare-to` against a run made under different
   prompt hashes prints the deltas with a prompt-change warning.
7. The benchmark file test fails when an objective is added that breaks a
   composition rule or reuses an ID.
8. Existing reports and API responses from before the migration still load;
   `regenerate_report` on an old discovery yields the `1.2` shape with
   `human_verdict: null` and metrics computed from its records.

## 9. Compatibility and decisions

- No behaviour of Finder, Validator, Judge, gates or connectors changes.
  Metrics observe; they do not block.
- Append-only verdicts and quality runs follow the repository's immutable
  revision pattern; nothing is updated in place.
- Report `1.1 → 1.2` and API detail `1.1 → 1.2` are additive. Stored
  `report_json` is regenerated on demand, not migrated.
- `DiscoveryVerdict.recorded_by` records the admin user or the API key's
  owner (`ApiKeyAuthentication` resolves a key to its user); it is nullable
  only so a deleted user does not take verdicts with it. `recorded_via` keeps
  the channel visible. `proposal` and `judgment` cascade with their rows:
  they are only deleted together with the discovery, and a protecting FK
  would make that cascade fail once a verdict exists.
- The metrics module is deterministic and versioned so a number from a
  November run can be compared with a December run only when the schema
  matches; the quality run row records the schema it used.
- Decided against an LLM grader for Phase 0: it would share blind spots with
  the roles and would itself need validation. Human verdicts plus
  deterministic metrics are the ground truth this phase establishes.
- Decided against reusing `FilterEvalRun`: it is tied to classifier
  precision/recall and prompt content; discovery quality has a different
  shape and lifecycle.
- The prototype-era test deferral in AGENTS.md does not apply to this work:
  each step ships its tests in the same commit, and the coverage floor is
  ratcheted after the phase.
