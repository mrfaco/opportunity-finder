# Investigation tools

This page is the source of truth for which tools the investigation agent can
actually call. Tools are MCP-shaped (`name`, `description`, JSON-Schema
`input_schema` / `output_schema`) and live in
[`agents/tools/stubs.py`](../agents/tools/stubs.py). The agent only sees tools
listed in `AGENT_TOOLSETS["investigation"]` (in
[`agents/tools/__init__.py`](../agents/tools/__init__.py)) — deferred stubs are
registered for documentation purposes but pruned from the toolset so the agent
never reaches for an unimplemented impl.

## Status

| Tool | Status | Auth | Purpose |
|---|---|---|---|
| `query_cluster` | ✅ live | — | Read the source cluster (always the agent's first call) |
| `query_related_clusters` | ✅ live | — | pgvector nearest-neighbour on centroids; spot adjacent investigations |
| `search_hacker_news` | ✅ live | — | Algolia HN search API |
| `fetch_hn_item` | ✅ live | — | HN item + comment tree (bounded) |
| `fetch_url` | ✅ live | — | General URL fetch, HTML→text, capped at ~200KB |
| `record_brief` | ✅ live | — | Termination signal: persists the `Brief` and creates an `Investigation` row |
| `web_search` | ✅ live | `TAVILY_API_KEY` | General web search via Tavily; for prior-art and competitor discovery |
| `search_github_issues` | ✅ live | `GITHUB_TOKEN` | Search GitHub issues for evidence of the same pain |
| `search_stack_overflow` | ✅ live | `STACKEXCHANGE_KEY` (optional) | Search Stack Overflow for workarounds and acknowledgments |
| `query_known_competitors` | ❌ deferred | requires new `Competitor` model | Look up catalogued competitors for a topic |
| `search_product_hunt` | ❌ deferred | `PRODUCT_HUNT_TOKEN` (OAuth) | Product Hunt v2 GraphQL search |
| `fetch_product_hunt_comments` | ❌ deferred | `PRODUCT_HUNT_TOKEN` | Reception sentiment from PH comment threads |
| `query_trustmrr` | ❌ deferred | `TRUSTMRR_API_KEY` (paid) | Public MRR / funding signal for a company |
| `summarize_text` | ❌ deferred | `ANTHROPIC_API_KEY` | Inline Haiku summary — lowest priority (Sonnet can summarize inline) |

## Coverage gaps the deferred set would close

- **`query_known_competitors`** assumes a curated `Competitor` table per
  category. Worth building once we have repeated investigations in the same
  vertical and want the agent to start with our own catalogue instead of
  rediscovering from scratch.
- **Product Hunt pair** would catch consumer / no-code launches that don't
  show up on HN/GitHub. Lower priority for a dev-tools-skewed pipeline.
- **`query_trustmrr`** is the only paid API in the deferred set; defer until
  briefs are good enough that revenue benchmarking actually changes a
  recommendation.
- **`summarize_text`** is genuinely lowest priority. Sonnet inside the loop
  already summarizes anything we feed it.

## How to add a tool

The pattern is small enough to memorise; see `_fetch_url`
in [`agents/tools/stubs.py`](../agents/tools/stubs.py) as the canonical
example.

1. **Schema** — declare `class XxxInput(ToolInput)` and
   `class XxxOutput(ToolOutput)` (subclasses of Pydantic `BaseModel`).
   Tighten `results: list[ConcreteHit]` to a typed model, not `list[dict]` —
   the JSON Schema goes straight to the model's tool definition.
2. **Impl** — `def _xxx(inp: XxxInput) -> XxxOutput`. Use `httpx` for HTTP.
   Let HTTP errors propagate via `raise_for_status()` (the loop records and
   re-raises; no fallbacks). For tools that require a key, fail loud on
   missing config:
   ```python
   if not settings.MY_KEY:
       raise RuntimeError("MY_KEY is not set. Configure it in .env.")
   ```
3. **Register** the `Tool(...)` instance via the module-level `register(...)`
   call right under the impl. `cost_tier` is `0=internal, 1=free external,
   2=paid external, 3=expensive`.
4. **Settings** — add `MY_KEY = env("MY_KEY", default="")` in
   [`config/settings.py`](../config/settings.py) next to the existing
   external-API block, plus a corresponding entry in
   [`.env.example`](../.env.example) with a one-line comment pointing at
   where to obtain the key.
5. **Expose** — add the tool name to
   `AGENT_TOOLSETS["investigation"]` in
   [`agents/tools/__init__.py`](../agents/tools/__init__.py). Until you do
   this the agent literally cannot see it (we prune deferred tools so an
   unimplemented impl never crashes a live run — see commit `b0e4bc9` for
   why).
6. **Test** — at minimum one parsing test (mock
   `agents.tools.stubs.httpx.get` / `httpx.post`, dispatch through
   `get_tool(name).dispatch(...)`, assert on the typed output). For tools
   that require a key, add a missing-key test too. Tests live in
   [`tests/test_tools.py`](../tests/test_tools.py).
7. **Update this doc.**
