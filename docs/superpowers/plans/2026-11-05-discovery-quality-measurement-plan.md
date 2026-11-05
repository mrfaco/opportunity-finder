# Discovery quality measurement implementation plan (roadmap Phase 0)

Date: 2026-11-05
Status: proposed; nothing implemented.
Specification: `../specs/2026-11-05-discovery-quality-measurement-design.md`.

## Delivery strategy

Seven steps, each a reviewable commit with its tests, on the existing Django,
Celery and PostgreSQL stack. Steps 1–6 need no model calls and no spend;
step 7 is the paid baseline and needs explicit authorization. Nothing in
this phase changes agent behaviour, so each step can ship independently
without affecting running discoveries.

The AGENTS.md prototype deferral does not apply here: new behaviour gets its
test in the same commit, tests use real PostgreSQL, and the coverage gate is
ratcheted at the end. Keep imports absolute and module-level, exceptions
loud, and every new structured artifact versioned.

Estimated effort: about eight working days for one engineer, excluding
baseline wall-clock time.

## 1. Human verdict record, admin and API

Files: `discovery/models.py`, generated `discovery/migrations/0003_*.py`,
`discovery/schemas.py`, `discovery/admin.py`,
`discovery/templates/admin/discovery/review.html`, `discovery/api.py`,
`discovery/urls.py`, `discovery/orchestrator.py` (regenerate hook),
`tests/test_discovery_verdict.py`.

- Add `DiscoveryVerdict` as specified (append-only; `proposal` and
  `judgment` references captured at write time; `recorded_via`).
- Add `VerdictInput` contract (`schema_version "1.0"`, `verdict`, `notes`)
  with the rule that `wrong` requires non-empty `notes`.
- Service function `record_verdict(discovery, verdict, notes, user, via)` in
  a new `discovery/verdicts.py`: refuses non-terminal discoveries with a
  typed error, writes the row, calls `regenerate_report`. Only this module
  writes `DiscoveryVerdict` rows.
- Admin: new `verdict/` URL on the discovery admin, POST only, change
  permission required; form at the top of the report panel on the review
  page; verdict history list beneath it. The review page itself stays GET.
- API: `POST /api/v1/discoveries/<id>/verdict/`; 201 with the record, 409
  on non-terminal, 400 on contract errors. Extend `discovery_view` with
  `human_verdict` and `verdict_count`; bump its `schema_version` to `1.2`.
- Update `docs/discovery.md` API table and admin section.

Tests: verdict on completed, failed and cancelled discoveries succeeds;
on queued and running is refused via admin and API with nothing written;
`wrong` without notes is rejected; two verdicts keep both rows and the API
reports the latest; admin POST requires change permission; report is
regenerated after a verdict.

Done: a reviewer can record and see verdicts; nothing else in the system
changed.

## 2. Quality metrics module

Files: `discovery/quality.py`, `discovery/schemas.py` (`QualityMetrics`),
`tests/test_discovery_quality.py`, a shared fixture builder in
`tests/discovery_fixtures.py` if the existing tests do not already provide
one that creates a discovery with proposals, validations, evidence and
research operations.

- Implement `compute_quality(discovery) -> QualityMetrics` exactly as the
  spec's metric table defines, reading only persisted rows.
- Implement `_query_tokens` and `_novelty(query, finder_queries)` as small
  pure helpers so the Jaccard measure is testable on its own and reusable by
  Phase 1's independence gate.
- Every metric that cannot be computed is `None` with a human-readable entry
  in `notes`; no silent zeros.
- No model calls, network or writes; mypy-clean; no exception suppression.

Tests: build a discovery with two origins, one snippet citation, two fact
claims (one quoted), Finder and Validator searches with a reworded and a
distinct query, one pivot, one accepted validation and one Judge approval;
assert every metric value by hand. Assert `None` plus a note for
`median_evidence_age_days` when `published_at` is missing and for the
independence metrics when Validator never searched. Assert content-hash
duplicates across domains count as one origin, matching `evidence_gaps`.

Done: metrics are reproducible on fixtures and documented by their tests.

## 3. Benchmark objective set

Files: `discovery/eval/__init__.py`, `discovery/eval/objectives.json`,
`discovery/eval/benchmark.py` (contract + loader + composition checks),
`tests/test_discovery_benchmark_set.py`.

- Write twelve objectives covering services, manufacturing, intelligence,
  goods and software across at least three regions and at least four
  non-English languages, each with the human rubric from the spec. Draft
  them so a reviewer without context can apply the rubric.
- `BenchmarkObjective` and `BenchmarkSet` contracts, `schema_version "1.0"`,
  `extra="forbid"`; `load_benchmark()` reads the JSON and runs the
  composition checks, raising a typed error naming the rule broken.
- Add `benchmark_tag` and `benchmark_objective_id` to `Discovery` (blank by
  default, indexed tag), migration `0004_*`, read-only in admin list filters
  and in `discovery_view`.

Tests: the committed set loads and passes every composition rule; a set
with a duplicate ID, fewer than four non-software objectives or two
identical `(region, focus)` pairs is rejected with the rule named.

Done: the benchmark is a validated, git-managed artifact.

## 4. Benchmark run command

Files: `discovery/management/commands/run_discovery_benchmark.py`,
`tests/test_run_discovery_benchmark.py`, `docs/discovery.md`.

- Dry run by default; `--authorize-spend` plus `--max-total-usd` required to
  start anything; refuse when the ceiling sum exceeds the total.
- Start through the existing idempotent start service with key
  `benchmark:<tag>:<objective_id>`; set `benchmark_tag` and
  `benchmark_objective_id` on the created discovery in the same transaction.
- Merge `--config-json` over deployment defaults and validate as
  `DiscoveryConfig` before any start; print frozen config and prompt hashes.
- Show the rubric on the admin review page when `benchmark_objective_id` is
  set (template change from step 1's page).

Tests (real PostgreSQL, Celery dispatch stubbed at the existing boundary,
no model calls): dry run prints twelve keys and creates nothing; authorized
run with a too-small ceiling refuses; authorized run creates twelve
discoveries with tags; a second run with the same tag creates none and
reports the existing IDs; a subset via `--objective` creates only those; an
invalid `--config-json` is rejected before any start.

Done: a benchmark can be started repeatably and cannot double-spend.

## 5. Scoring command and quality run record

Files: `discovery/models.py` (`DiscoveryQualityRun`), migration `0005_*`,
`discovery/management/commands/score_discoveries.py`,
`discovery/quality.py` (aggregate helpers), `tests/test_score_discoveries.py`.

- Select by `--tag`, `--discovery` or `--since`; score terminal discoveries;
  list skipped non-terminal ones.
- Persist one `DiscoveryQualityRun` per invocation with aggregate (mean,
  median, min, max, count, `None` count per metric), per-discovery dumps,
  distinct prompt hashes per role and the count of distinct config snapshots.
- `--compare-to <run id>`: per-metric deltas; a warning line whenever the
  prompt hashes or metrics schema differ between the two runs.
- `--format markdown` (default) prints a table suitable for pasting into
  `docs/discovery-eval.md`; `--format json` prints the persisted row.

Tests: scoring three fixture discoveries (two terminal, one running) writes
one run with `count == 2` and names the skipped one; aggregates exclude
`None` and report the exclusion; `--compare-to` prints deltas and the
prompt-change warning when hashes differ and no warning when they match;
JSON output round-trips through the `QualityMetrics` contract.

Done: two runs can be compared from persisted rows without recomputation.

## 6. Report, API exposure and documentation

Files: `discovery/reporting.py`, `discovery/schemas.py` (`Report` → `1.2`),
`discovery/api.py`, `discovery/templates/admin/discovery/review.html`,
`docs/discovery.md`, new `docs/discovery-eval.md`, `AGENTS.md` (one
paragraph: metrics observe, gates enforce; verdict and quality rows are
append-only and written only by their service modules), `CLAUDE.md` (one
line pointing at the eval doc), `pyproject.toml` via `make coverage-ratchet`.

- "Evidence quality" subsection under "Limitations and provenance" with the
  evidence and independence metrics in plain language and the human verdict
  line under "Final assessment".
- `Report` gains `quality` and `human_verdict`; `discovery_view` gains
  `quality` for terminal discoveries (null otherwise).
- Admin review page shows the metrics table above the report.
- `docs/discovery-eval.md`: how to run the benchmark, how to score, the
  comparison discipline (same tag family, note schema bumps), and an empty
  baseline table ready for step 7.
- Run `make coverage-ratchet` after the suite passes.

Tests: `regenerate_report` on an existing fixture discovery yields schema
`1.2` with `human_verdict` null and populated metrics; a discovery with a
verdict renders it in Markdown; the API detail carries `quality` only when
terminal; the old `1.1` `report_json` still deserializes where it is read.

Done: every surface that shows a discovery also shows how well evidenced it
was and what a human thought of it.

## 7. Baseline (human-authorized, paid)

Files: `docs/discovery-eval.md` (baseline row), nothing in code.

- Operator confirms worker, Claude Code, OpenRouter and Ollama configuration
  and authorizes a total ceiling (twelve objectives at USD 10 is USD 120).
- `run_discovery_benchmark --tag baseline-2026-11 --authorize-spend
  --max-total-usd 120`.
- When all twelve are terminal: a reviewer records a human verdict on each
  using the rubric shown on the review page; then
  `score_discoveries --tag baseline-2026-11 --format markdown`.
- Paste the aggregate, verdict distribution, prompt hashes, config and total
  known and estimated cost into `docs/discovery-eval.md`. Note any discovery
  that stopped on a limit rather than converging; that is part of the
  baseline, not an outlier to remove.

Done: a dated baseline exists that Phases 1–3 can be compared against.

## Primary implementation risks

- Fixture realism: metrics tested only on synthetic discoveries can hide
  edge cases in real records (empty claims lists, evidence with no origin,
  legacy schema-1.0 runs). Mitigation: step 2 also runs `compute_quality`
  over every discovery in the development database as a smoke check and
  asserts no exception, and the legacy batch smoke data is part of the
  fixture set.
- Benchmark cost and time: twelve runs at up to 100 turns each can take
  hours and approach the ceiling. Mitigation: the command never starts
  more than the authorized total; the baseline is run once, overnight, and
  partial results are still scored.
- Reviewer bandwidth: twelve verdicts with rubric reading is a real
  afternoon of work. Mitigation: the rubric is on the review page, and
  `untested` is a legitimate verdict.
- Jaccard novelty is crude: short queries and shared domain terms produce
  low novelty for legitimately independent searches. Mitigation: it is
  reported, not enforced, in Phase 0; Phase 1 decides the threshold from
  baseline data rather than guessing.
- Migration count: three migrations (`0003`–`0005`) across steps. Acceptable
  for reviewable commits; do not squash by hand.

## Review assumptions before implementation

- A verdict belongs to the discovery, not to a specific proposal, but
  records which proposal and judgment were current. Confirm this is the
  granularity reviewers want; an alternative is one verdict per proposal
  revision, which is heavier and rarely needed.
- API-key callers may record verdicts; the key's owner is recorded as
  `recorded_by` with `recorded_via` `api`. Confirm that is acceptable or
  restrict verdicts to admin users.
- Twelve objectives is the first set size. Confirm the regions and
  languages to prioritize (the roadmap's open decision on Argentina-first
  applies here too).
- The baseline uses current defaults even though Finder and Validator share
  a model family. That is deliberate: the baseline measures the system as
  deployed. Confirm no default is changed before the baseline is recorded.
