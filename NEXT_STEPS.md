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

## 2. Plug in a real embedding model — ✅ DONE

`clusters/embeddings.py` embeds text via Voyage AI (`voyage-3.5`, 1024-dim
native — matches the pgvector columns). `clustering.compute_embedding`
delegates to it. `embed_texts` batches and fails loud if the model returns
the wrong dimensionality. The `reembed_cluster_items` management command
re-embeds all items and recomputes centroids after a model change. Needs a
`VOYAGE_API_KEY` in `.env`.

## 3. Implement the Hacker News ingestion adapter — ✅ DONE

`ingestion/adapters/hacker_news.py` pulls Ask HN stories via the Algolia HN
search API. `ingestion/pipeline.py` runs each item through
classify → embed → cluster and writes a `FilterClassification` per item
(kept or discarded). A per-source `IngestionCheckpoint` makes runs
incremental and crash-safe. `ingest_source` is wired to the adapter
registry; `manage.py setup_schedules` seeds the hourly ingestion + nightly
refinement Celery Beat tasks.

Deferred: comment ingestion (higher volume — needs a keyword pre-filter
before the classifier call). Tracked loosely under step 10.

## 4. Implement the `query_cluster` tool — ✅ DONE

`query_cluster` is now real. Inputs: `cluster_id`, optional `max_items` (5).
Returns a structured `ClusterSummary` (id, status, title, summary, size,
sources, category_tags, classifier_score, first/last_seen_at) plus a sample
of the highest-confidence member items as `ClusterItemBrief` (id, source,
url, title, author, posted_at, snippet, classifier_confidence). Returns
`status='not_found'` for missing or non-UUID ids — the agent's
structured-error contract, not a fallback.

Implementation reads live cluster state by id; per-run snapshot semantics
for tool reads is deferred (would require threading run context through
``Tool.dispatch``).

## 5. Author `prompts/investigation/system.md` and `procedural.md` v1.0 — ✅ DONE

`system.md` carries the role, what counts as an opportunity, the
investigation strategy (which tools to call in what order), the honesty
rules (no fabrication, cite or hedge, calibrated confidence), termination
criteria, and budget awareness.

`procedural.md` carries the seven-question rubric, the field-by-field
brief structure (matching `investigations.schemas.Brief`), evidence rules,
confidence calibration bands, output format guidance, and a worked-example
sketch.

The loop in step 7 will decide the exact output mechanism (forced
`record_brief` tool vs. `messages.parse` with the `Brief` Pydantic model);
the prompts describe the brief semantically so either path works.

## 6. Plumb real Anthropic API calls through the filter classifier — ✅ DONE

Done as part of step 1. `ingestion.filter.classify_content` uses the
Anthropic SDK with prompt caching and structured output, and records token
counts + latency on every `FilterVerdict`. The remaining wiring is the
ingestion pipeline that persists a `FilterClassification` per ingested item
— that lands with step 3 (the Hacker News adapter).

## 7. Plumb real Anthropic API calls through the investigation agent loop — ✅ DONE

`agents.loop._call_model` is a live Anthropic SDK call. The system prompt
+ tools are cached as the stable prefix (top-level `cache_control`); the
loop converts the MCP-shape tool list into the SDK's shape and lifts the
`role: "system"` history entry out into the SDK's `system=` parameter.

The agent signals termination by calling the new ``record_brief`` tool
whose input type is ``investigations.schemas.Brief`` — the JSON schema is
enforced by Pydantic. The loop intercepts that call, persists the brief
onto ``AgentRun.final_output``, sets termination to
``AGENT_DECIDED_DONE``, and creates an ``Investigation`` row in
``awaiting_review`` status linking back to the run. A degraded text-only
end-turn is still accepted but does not create an Investigation.

7 new tests (mocked Anthropic SDK + real query_cluster tool against the
test DB) exercise the model-call shape, prompt-cache headers, full
two-turn loop with brief recording, the tool_use ↔ tool_result history
round-trip, and the record_brief tool registration. Coverage gate
ratcheted 72% → 80%.

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
