# Evidence substance implementation plan (roadmap Phase 1)

Date: 2026-11-05
Status: proposed; nothing implemented.
Specification: `../specs/2026-11-05-evidence-substance-design.md`.
Prerequisite: Phase 0 merged (`discovery/quality.py`, the benchmark and
`score_discoveries`). The paid baseline (Phase 0 step 7) is needed before
step 7 here, not before steps 1–6.

## Delivery strategy

Seven steps; steps 1–6 are unpaid, each a reviewable commit with its tests,
and leave every default unchanged so running discoveries and the Phase 0
baseline are unaffected. Step 7 is the paid comparison that decides whether
the stricter policy becomes the default; that flip is its own commit.

Order matters: typing (1) and the proposal kind (3) are inputs to the gate
(4); the metrics (6) read everything before them. Steps 2 and 5 are
independent of the others and can be done in parallel by a second engineer
on a branch, as they touch `web.py`/`pdf.py` and the search connectors
respectively.

AGENTS.md applies in full: tests in the same commit on real PostgreSQL,
absolute imports, loud failures, schema bumps with their migration or reader
in the same commit, coverage ratcheted at the end. Connectors stay read-only
and do not classify.

Estimated effort: ten to twelve working days for one engineer; seven to
eight with the parallel split above.

## 1. Source typing

Files: new `discovery/source_type_rules.json`, new `discovery/source_types.py`,
`discovery/models.py` (+ migration `0007_evidence_source_type`),
`discovery/research.py`, `discovery/agent_tools.py`, `discovery/loop.py`,
`discovery/admin.py` + `review.html`, `discovery/reporting.py` (Source
register only), new `discovery/management/commands/classify_evidence.py`,
`docs/discovery.md`, `tests/test_source_types.py`,
`tests/test_classify_evidence.py`.

- Write the rules file with the lists in spec §2 and a `rules_version`;
  contract + loader + `classify()` in `source_types.py`, first match wins in
  the spec's order. Keep lists sorted and commented by purpose.
- Add `Evidence.source_type` (indexed, default `unclassified`); classify at
  write time in `research.py` next to `origin`; stamp
  `source_type_rule` and `source_type_rules_version` into metadata; new rows
  `schema_version "1.1"`.
- Expose `source_type`, `origin`, `published_at` in `model_result`
  documents, in `_initial_context["evidence"]`, in the admin evidence table
  and in the report's Source register.
- `classify_evidence` command: dry run by default, `--apply` updates rows
  whose rules version differs, prints per-type change counts, idempotent.

Tests: one URL per type plus the ordering cases (`.gob.ar` tender vs
regulator, statistics office vs regulator, `/pricing` vendor vs
`unclassified` home page); rules file validates and every listed domain
classifies to its own list; research path stamps type and metadata; tool
result and context include the fields; backfill dry run writes nothing and
`--apply` is idempotent.

Done: every evidence row has a type the roles and the report can see.

## 2. Freshness

Files: `discovery/connectors/web.py`, `discovery/connectors/pdf.py`,
`discovery/reporting.py`, `tests/test_research_freshness.py`.

- `_published_at(html, headers) -> tuple[str, str] | None` trying the
  sources in spec §3 order; strict parsing; future dates (beyond one day)
  rejected; returns ISO timestamp and source name.
- Set `ResearchDocument.published_at` and
  `metadata["published_at_source"]` for HTML pages; for PDFs read
  `CreationDate`/`ModDate` from the `pdfinfo` output already captured.
- Report: Source register prints `published <date> (<source>)` and age;
  Limitations lists cited evidence older than `STALE_EVIDENCE_MONTHS = 24`.

Tests: fixture pages for each date source and precedence between them;
malformed and future dates ignored; `Last-Modified` labelled; PDF
creation date; report renders the age line and the stale limitation
without affecting acceptance.

Done: evidence age is known whenever the source states it, and visible.

## 3. Business-model kind on the Proposal

Files: `discovery/schemas.py` (Proposal 1.1), `prompts/finder/system.md`
(1.2, one sentence), `discovery/reporting.py`, `discovery/termination.py`
(a `proposal_kind(content) -> str` helper returning `unspecified` for 1.0
content), `tests/test_proposal_kind.py`; `make validate-prompts`.

- Add `business_model_kind` as a required Literal on `Proposal`; keep
  `business_model` text.
- The Finder prompt names the field and its values in one sentence; no
  other prompt change in this phase.
- Report prints the kind under the final proposal; `proposal_kind` is the
  only reader of the field so old content is handled in one place.

Tests: contract rejects a proposal without the field and an unknown value;
1.0 content yields `unspecified`; the report renders both; the Finder tool
schema advertises the field.

Done: a proposal's business model is comparable without parsing prose.

## 4. Evidence policy and independence

Files: `discovery/schemas.py` (DiscoveryConfig 1.3), `discovery/admin.py`
(start form fields), new `discovery/independence.py` (novelty helpers moved
from `quality.py`, which imports them), `discovery/termination.py`,
`discovery/agent_tools.py` (`SubmissionError.finder_must_revise` replaces the
exact-string match for `recovery_hint`), `discovery/quality.py`,
`docs/discovery.md`, `tests/test_evidence_policy.py`,
`tests/test_independence.py`.

- Config: `evidence_policy` and `min_validator_query_novelty` with the
  defaults and migration rule in spec §5; shown in the start form and the
  report's configuration block.
- `evidence_gaps(discovery, proposal)` reads the policy from the snapshot;
  `typed` adds rules 1–2 with the actionable messages naming IDs and types;
  `origins` is byte-for-byte today's behaviour.
- `has_independent_research` uses `independence.novelty` with the frozen
  threshold; the failure message reports the best novelty and the threshold.
- `SubmissionError(message, finder_must_revise=True)` for every gap that
  only Finder can fix; `agent_tools` adds the hint from the flag and names
  the missing type and the enabled connectors likely to supply it.

Tests: policy defaults and snapshot migration for 1.0–1.2 configs; under
`origins` the existing termination tests pass unchanged; under `typed` each
rule accepts and refuses the fixtures in spec §9 items 5–7; recovery hint
present for every flagged error and absent otherwise; novelty threshold
0.0 reproduces today's results on the existing fixtures.

Done: the stricter gate exists, is frozen per run, and is off by default.

## 5. Search filters and budget

Files: `discovery/connectors/web.py` (DuckDuckGo), `discovery/connectors/search.py`
(Bing, `search_auto`), `discovery/research.py` (`ResearchInput.filters`
description, routed-backend counting, `filters_applied`/`filters_dropped`),
`discovery/loop.py` (tool description), `docs/discovery.md`,
`tests/test_search_filters.py`; update
`tests/test_research_pdf_search.py::test_fallback_cannot_exceed_research_quota`.

- Declare and map `country`, `language`, `recency` per spec §6; a small
  country→default-language table for DuckDuckGo's `kl`; values validated
  before any request.
- `search_auto` passes the filters through, drops `recency` for Bing with an
  audit record in the attempt and at the top level, never silently.
- `research_calls_used` groups `:backend:` operations under their parent
  key; remove the fallback pre-check; `research_calls_remaining` follows.

Tests: each mapping asserted on the outgoing request (mock transport at the
HTTP boundary, as the existing connector tests do); invalid values refused;
fallback with recency records the drop; eight fallback pairs fit in an
eight-call budget and the ninth search is refused; the updated quota test
asserts the new rule.

Done: the free search can target a market and a period, and a fallback no
longer halves the research budget.

## 6. Metrics 1.1, report, docs

Files: `discovery/schemas.py` (QualityMetrics 1.1), `discovery/quality.py`,
`discovery/management/commands/score_discoveries.py` (new metrics in the
table), `discovery/reporting.py`, `docs/discovery-eval.md`, `AGENTS.md` (one
line: classification is deterministic and lives in the research service),
`pyproject.toml` via `make coverage-ratchet`, `tests/test_discovery_quality.py`
(extend), `tests/test_discovery_report_quality.py` (extend).

- Add the §7 metrics; `benchmark_focus_match` reads the objective from the
  benchmark set by `benchmark_objective_id`.
- Evidence quality section prints type counts and the two shares; the eval
  doc gains a "1.1 metrics without a 1.0 counterpart" note and the step 7
  procedure.
- Ratchet coverage after the suite passes.

Tests: fixtures under both policies produce the expected metric values;
`None` with notes where undefined; `--compare-to` across 1.0 and 1.1 runs
warns; report prints the new lines.

Done: the benchmark can show whether evidence substance moved.

## 7. Benchmark comparison and default decision (human-authorized, paid)

Files: `docs/discovery-eval.md` (comparison row); then, only on a go
decision, `config/settings.py`, `.env.example`, `discovery/schemas.py`
defaults, `docs/discovery.md`.

- Confirm the Phase 0 baseline row exists for the current objective set.
- `run_discovery_benchmark --tag phase1-typed-<yyyy-mm> --authorize-spend
  --max-total-usd 120 --config-json '{"evidence_policy": "typed",
  "min_validator_query_novelty": 0.4}'`; human verdicts on all twelve;
  `score_discoveries --tag phase1-typed-<yyyy-mm> --compare-to <baseline run>`.
- Record the row with the deltas on `demand_independent_source_share`,
  `competitor_offer_source_share`, `validator_query_novelty_min`, the
  outcome distribution, cost per approved proposal and the verdict split.
- Decide: flip both defaults, flip one, adjust the threshold and re-run, or
  keep `origins`. The flip commit cites both quality-run IDs.

Done: the deployment default is whatever the comparison justified, and the
reasoning is in git.

## Primary implementation risks

- **Domain lists are never complete.** A legitimate independent source
  that is `unclassified` fails the typed demand rule. Mitigation: the gate
  message names the cited types so Finder can look for a classifiable
  source; the quarterly rules review adds what the misclassified set shows;
  the policy is off until the comparison says the trade-off is acceptable.
- **Date extraction is heuristic.** Wrong dates are worse than none.
  Mitigation: strict parsing, future-date rejection, the date source is
  always recorded and shown, `Last-Modified` labelled as weak, staleness is
  observation-only.
- **Required proposal field changes Finder's output contract.** A model
  that ignores the new field fails validation on its first submission.
  Mitigation: the tool schema marks it required with an enum, the prompt
  names it, and the validation error is a normal self-correction input;
  verify on the first live turn after deploy.
- **Novelty threshold is sensitive to query length.** Short queries share
  most tokens with any Finder query on the same topic. Mitigation: default
  `0.0`; the baseline's novelty distribution sets the proposal for step 7.
- **Budget counting change interacts with historical runs.** Grouping is
  computed from operation keys at read time, so old turns are recounted
  under the new rule when inspected; that only lowers the count. Document
  it in the eval doc.

## Review assumptions before implementation

- `vendor` is detected by URL path only, and a seller's home page is
  `unclassified`; both fail the independent-demand rule. Confirm that is
  the intended strictness, or whether `unclassified` demand evidence should
  pass when accompanied by one classifiable source elsewhere in the claims.
- The offer-level competitor rule applies to `services`, `manufacturing`,
  `goods` and `marketplace`, not to `software` or `intelligence`. Confirm.
- `min_validator_query_novelty` starts at `0.4` in the comparison run.
  Confirm, or wait for the baseline's novelty distribution before choosing.
- `recency` is dropped for Bing with an audit record rather than refusing
  the fallback. Confirm.
- The news-outlet list covers the benchmark regions (AR, MX, BR, DE, FR, ES,
  US, IN) plus international business press; other regions start
  `unclassified`. Confirm the first list's scope.
