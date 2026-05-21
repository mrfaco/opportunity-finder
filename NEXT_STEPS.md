# Next steps

The v1 commit ships a runnable skeleton. The work below is the path to a real,
end-to-end system, in the order that makes earlier work pay off as quickly as
possible.

## 1. Author `prompts/filter/classifier.md` v1.0 + seed eval set — ✅ DONE

The classifier prompt is authored in `prompts/filter/classifier.md`. The
200-item seed eval set lives as version-controlled JSON under
`ingestion/eval_data/` (50 per difficulty tier), loaded via
`manage.py load_eval_set`. `ingestion.filter.classify_content` makes a live
Haiku call with prompt caching and structured output; `run_filter_eval`
classifies the whole set and records precision/recall/F1 (overall and
per-tier) into a `FilterEvalRun`. Run it once an `ANTHROPIC_API_KEY` is set:

```sh
make migrate
make shell  # then: from ingestion.tasks import run_filter_eval; run_filter_eval()
```

Remaining polish for a later pass: the keyboard-driven labeling UI at
`/admin/ingestion/filter-labeling/` is still a placeholder, and the eval set
is hand-curated rather than drawn from production classifications (that
arrives once the ingestion adapters land).

## 2. Plug in a real embedding model

`compute_embedding` in `clusters/clustering.py` currently emits a
deterministic random vector. Replace with Voyage / Titan v2 / OpenAI
text-embedding-3-large reduced to 1024-dim. Backfill any existing items.

## 3. Implement the Hacker News ingestion adapter

`ingestion/adapters/hacker_news.py` is the first real adapter. Pull Ask HN
threads + comment text, identify candidate items via lightweight signal
heuristics, push them through the classifier and clustering pipeline.
Wire `ingest_source` to drive it.

## 4. Implement the `query_cluster` tool

The investigation agent's first tool call is almost always `query_cluster`.
Implementing this alone unlocks "manual investigation runs from the admin"
once the model wrapper is in place.

## 5. Author `prompts/investigation/system.md` and `procedural.md` v1.0

Role + goal + termination criteria in `system.md`; seven-question rubric,
brief structure, evidence rules in `procedural.md`. Cross-reference
`investigations/schemas.py::Brief` for the structured contract.

## 6. Plumb real Anthropic API calls through the filter classifier — ✅ DONE

Done as part of step 1. `ingestion.filter.classify_content` uses the
Anthropic SDK with prompt caching and structured output, and records token
counts + latency on every `FilterVerdict`. The remaining wiring is the
ingestion pipeline that persists a `FilterClassification` per ingested item
— that lands with step 3 (the Hacker News adapter).

## 7. Plumb real Anthropic API calls through the investigation agent loop

`agents.loop._call_model` is the last piece. Use the SDK with tools,
prompt caching, and structured-output parsing of the final brief against
`Brief.json_schema()`.

## 8. Implement the rest of the tools in priority order

Recommended order based on agent-call frequency: `query_related_clusters`,
`fetch_hn_item`, `search_hacker_news`, `web_search`, `fetch_url`,
`search_github_issues`, `search_stack_overflow`, `search_product_hunt`,
`fetch_product_hunt_comments`, `query_known_competitors`,
`query_trustmrr`, `summarize_text`.

## 9. Implement the LLM judge for merge/split proposals

`clusters.clustering.llm_judge_merge` and `llm_judge_split` are stubs
today. Implement against Haiku with a binary "same underlying need" prompt.
The proposal queues already exist; once the judge is in place the human
approval flow works end-to-end.

## 10. Implement remaining ingestion adapters

GitHub, Stack Overflow, Product Hunt — same pattern as Hacker News. Each
should batch-classify and respect rate limits.

## 11. Polish the trajectory viewer

`/admin/agents/run/<id>/trajectory/` renders today but is intentionally
sparse. Add collapsible payloads, side-by-side model + tool view, syntax
highlighting, and per-event copy buttons.

## 12. Add cross-run caching for `fetch_url`, `fetch_hn_item`, `query_trustmrr`

The run-scoped cache works fine for within-run dedupe. The next win is
shared persistent caches keyed by URL / HN item id / company — long TTLs
on success, short TTLs on errors. Define the shared cache namespace
alongside the run-scoped one in `agents/cache.py`.
