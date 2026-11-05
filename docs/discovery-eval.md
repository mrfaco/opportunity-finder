# Discovery quality evaluation

How to tell whether a change to Finder, Validator, Judge, prompts or
connectors made discovery results better. Specification:
`superpowers/specs/2026-11-05-discovery-quality-measurement-design.md`.

Three sources of truth, all persisted and none needing a model call:

1. **Human verdicts.** `good`, `weak`, `wrong` (notes required) or `untested`,
   recorded on a terminal discovery from the admin review page or
   `POST /api/v1/discoveries/<id>/verdict/`. Append-only; the newest is current.
2. **Quality metrics.** `discovery/quality.py` computes `QualityMetrics`
   (schema 1.0) from stored proposals, reviews, evidence and research
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

## Results

| Tag | Date | Quality run | Prompt hashes (F / V / J) | Config | Scored | Verdicts (good / weak / wrong / untested) | Known USD | Estimated USD | Key metrics | Notes |
|---|---|---|---|---|---|---|---|---|---|---|

The baseline row (`baseline-2026-11`) is recorded after the authorized
benchmark run. Every later phase adds its row and its delta against the
baseline.

## Retired objectives

None.
