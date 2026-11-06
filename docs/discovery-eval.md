# Discovery quality evaluation

How to tell whether a change to Finder, Validator, Judge, prompts or
connectors made discovery results better. Specification:
`superpowers/specs/2026-11-05-discovery-quality-measurement-design.md`.

Three sources of truth, all persisted and none needing a model call:

1. **Human verdicts.** `good`, `weak`, `wrong` (notes required) or `untested`,
   recorded on a terminal discovery from the admin review page or
   `POST /api/v1/discoveries/<id>/verdict/`. Append-only; the newest is current.
2. **Quality metrics.** `discovery/quality.py` computes `QualityMetrics`
   (schema 1.3) from stored proposals, reviews, evidence and research
   operations. A metric that cannot be computed is `None` with a reason in
   `notes`, never zero. Metrics observe; acceptance gates enforce.
3. **Quality runs.** `score_discoveries` persists an append-only
   `DiscoveryQualityRun` with per-discovery metrics and aggregates, so runs
   are compared without recomputation.

## Benchmark set

`discovery/eval/objectives.json` (schema 1.0) holds twelve objectives with a
reviewer rubric each. A test enforces the composition rules: unique IDs, at
least four objectives without software in their focus, at least four not in
English, at least three regions, and no repeated `(region,
business_model_focus)` pair. IDs are stable: retire an objective by removing
it and recording why under "Retired objectives" below; never reuse an ID.

## Running the benchmark

The run is paid. Confirm worker, Claude Code, OpenRouter and Ollama
configuration first, and get explicit authorization for the total ceiling
(twelve objectives at the default USD 10 is USD 120).

```sh
python manage.py run_discovery_benchmark --tag baseline-2026-11          # dry run
python manage.py run_discovery_benchmark --tag baseline-2026-11 \
    --authorize-spend --max-total-usd 120
```

The dry run prints the frozen config, role prompt hashes, the ceiling sum and
the idempotency keys `benchmark:<tag>:<objective_id>`. Re-running a tag never
starts a second discovery for an existing key. The command does not wait.

## Reviewing and scoring

When every benchmark discovery is terminal:

1. Open each on the admin review page. The objective's rubric is shown beside
   the verdict form. Record a verdict on the opportunity, not on formatting.
   `untested` is a legitimate verdict.
2. Score and paste the Markdown output into the table below:

   ```sh
   python manage.py score_discoveries --tag baseline-2026-11 --format markdown
   ```

3. Note any discovery that stopped on a limit rather than converging; it is
   part of the result, not an outlier to remove.

## Comparison discipline

- Re-run the same objectives under a new tag in the same family
  (`baseline-2026-11`, `phase1-…`) and compare with
  `score_discoveries --tag <new> --compare-to <baseline run id>`.
- The command warns when prompt hashes, discovery configurations or the
  metrics schema differ between runs. A prompt change is often the point of
  the comparison; the warning keeps it labelled.
- A metrics schema bump invalidates numeric comparison for the metrics it
  changed. Say so in that row's notes.
- Query novelty is a crude token Jaccard with no stop-word list in 1.0; read
  it as a signal, not a threshold.
- Research budget counting groups a `search_auto` fallback pair into one
  slot at read time, so turns recorded before roadmap Phase 1 are recounted
  under the new rule when inspected; that can only lower their count.

### Metrics 1.1 without a 1.0 counterpart

Roadmap Phase 1 (evidence substance) bumped the metrics schema to 1.1. These
metrics have no 1.0 counterpart, so a comparison against a 1.0 run shows them
without an earlier mean (`score_discoveries --compare-to` lists them under
the schema warning):

- `cited_source_type_counts` — cited rows per `source_type`.
- `demand_independent_source_share` — demand citations of an independent type
  (forum, review, job post, tender, regulator, statistics, news, marketplace
  listing) ÷ demand citations.
- `competitor_offer_source_share` — competitor citations of an offer-level type
  (vendor, marketplace listing, job post, tender, review) ÷ competitor
  citations.
- `stale_cited_share` — cited rows older than 24 months at retrieval ÷ cited
  rows with a known date; unknown when none is dated.
- `business_model_kind` — from the final proposal; `unspecified` for proposals
  stored under contract 1.0.
- `benchmark_focus_match` — whether that kind is in the benchmark objective's
  `business_model_focus`; not applicable off-benchmark.
- `evidence_policy`, `min_validator_query_novelty` — from the frozen config.
  The command warns when one quality run mixes evidence policies.

Source types are assigned at capture time by the rules in
`discovery/source_type_rules.json`. Discoveries captured before Phase 1 read
as `unclassified` until `manage.py classify_evidence --apply` is run, which
makes their 1.1 shares meaningful; run it before scoring a pre-Phase-1 tag.

### Metrics 1.2 without a 1.1 counterpart

Roadmap Phase 2 (sources that show buying) bumped the metrics schema to 1.2 with
the exit-gate numbers for the new connectors. A comparison against a 1.1 run
shows them without an earlier mean and lists them under the schema warning; a
stored 1.1 record reads them as unknown:

- `transaction_signal_cited` — whether any cited evidence is typed `job_post`,
  `tender` or `marketplace_listing` (someone paying or offering a price);
  unknown without a final proposal. Aggregated as a share of discoveries.
- `specialist_connector_share` — cited rows from connectors other than general
  search and fetch (`web_fetch`, `web_search`, `search_auto`, `duckduckgo`,
  `bing`, `brave`) ÷ cited rows. A `wayback` snapshot counts as specialist.

### Metrics 1.3: model family

Roadmap Phase 3 (reasoning quality) bumped the metrics schema to 1.3 with one
semantic change and no new field. `same_model_family` now compares families
from `discovery.schemas.model_family` instead of exact provider and model
strings: the OpenRouter vendor prefix before `/`, `anthropic` for Anthropic,
and the Ollama tag before the first `-`, `:` or digit. Two DeepSeek variants,
such as OpenRouter `deepseek/deepseek-v4.1-flash` and Ollama
`deepseek-v4.1-flash:cloud`, now count as one family. A comparison against a
1.2 run warns about the schema change; read the `same_model_family` delta
across it as a definition change, not a behaviour change. Configs from before
per-role providers are read as OpenRouter. The report's limitation and the
start form's non-blocking warning use the same rule.

### Source comparison (roadmap Phase 2, step 9)

Paid and human-authorized. It isolates the effect of the new default connectors
(`search_auto,web_fetch,reddit,news_gdelt,open_data_ckan,wayback`) from the
Phase 1 gate by keeping the baseline's evidence policy.

1. Confirm the baseline row exists below and was scored under `origins`.
2. Run the benchmark with the new default connectors and `evidence_policy`
   `origins` (dry run first, then with spend authorization):

   ```sh
   python manage.py run_discovery_benchmark --tag phase2-sources-<yyyy-mm>
   python manage.py run_discovery_benchmark --tag phase2-sources-<yyyy-mm> \
       --authorize-spend --max-total-usd 120
   ```

3. Record human verdicts on all twelve, then score against the baseline:
   `score_discoveries --tag phase2-sources-<yyyy-mm> --compare-to <baseline run id>`.
4. Add a row with the deltas on `transaction_signal_cited`,
   `specialist_connector_share`, `cited_source_type_counts`,
   `demand_independent_source_share`, cost and the verdict split, and note which
   connectors the roles actually called (`cited_connector_counts` and the
   research ledger).
5. Decide the Tier 2b order from use: if job and tender types are cited,
   schedule `jobs_jooble`, `tenders_ted` and `tenders_sam`; if marketplace
   evidence is missing for goods proposals, `mercadolibre` first.

### Evidence-policy comparison (roadmap Phase 1, step 7)

Paid and human-authorized. The defaults stay `evidence_policy="origins"` and
`min_validator_query_novelty=0.0` until this comparison justifies a change.

1. Confirm the baseline row for the current objective set exists below.
2. Run the benchmark under the typed policy (dry run first, then with spend
   authorization):

   ```sh
   python manage.py run_discovery_benchmark --tag phase1-typed-<yyyy-mm> \
       --config-json '{"evidence_policy": "typed", "min_validator_query_novelty": 0.4}'
   python manage.py run_discovery_benchmark --tag phase1-typed-<yyyy-mm> \
       --authorize-spend --max-total-usd 120 \
       --config-json '{"evidence_policy": "typed", "min_validator_query_novelty": 0.4}'
   ```

3. Record human verdicts on all twelve, then score against the baseline:
   `score_discoveries --tag phase1-typed-<yyyy-mm> --compare-to <baseline run id>`.
4. Add a row with the deltas on `demand_independent_source_share`,
   `competitor_offer_source_share`, `validator_query_novelty_min`, the outcome
   distribution, cost per approved proposal and the verdict split.
5. Decide: flip both defaults, flip one, adjust the threshold (0.4 is a
   starting proposal; the baseline's novelty distribution may move it) and
   re-run, or keep `origins`. A flip is its own commit citing both quality-run
   IDs.

## Results

| Tag | Date | Quality run | Prompt hashes (F / V / J) | Config | Scored | Verdicts (good / weak / wrong / untested) | Known USD | Estimated USD | Key metrics | Notes |
|---|---|---|---|---|---|---|---|---|---|---|

The baseline row (`baseline-2026-11`) is recorded after the authorized
benchmark run. Every later phase adds its row and its delta against the
baseline.

## Retired objectives

None.
