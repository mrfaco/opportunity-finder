# Evidence substance (roadmap Phase 1)

Date: 2026-11-05
Status: proposed; nothing implemented. Depends on Phase 0
(`2026-11-05-discovery-quality-measurement-design.md`) for the metrics module,
the benchmark and the baseline it is compared against.
Implementation plan: `../plans/2026-11-05-evidence-substance-plan.md`.

## 1. Outcome and scope

Today the acceptance gate asks how many hostnames a proposal cites, not what
kind of source they are or how old. Two vendor blog posts pass. After this
phase the system knows, deterministically and for every evidence row, what
kind of source it is and when it was published; the roles see that; the
report prints it; and a stricter, configurable gate can require independent
demand evidence and offer-level competitor evidence. The stricter gate ships
**off by default** and is switched on only after a benchmark comparison
against the Phase 0 baseline, because this phase must not trade false
acceptances for a flood of "insufficient evidence" runs without measuring.

In scope:

1. **Source typing** — a `source_type` on every `Evidence` row, derived from
   connector, document kind, hostname and URL path by versioned rules in git.
2. **Freshness** — `published_at` for web pages and PDFs, evidence age in
   the report, stale citations listed as a limitation.
3. **Business-model kind** — a structured `business_model_kind` on the
   Proposal so a rule can ask "is this a services or goods business" without
   parsing prose.
4. **Evidence policy** — `DiscoveryConfig` 1.3 adds `evidence_policy`
   (`origins`, today's rule; `typed`, the stricter rule) and
   `min_validator_query_novelty`; gates read them from the frozen snapshot.
5. **Real independence** — `has_independent_research` measures query
   novelty with the Phase 0 token-overlap helper instead of string
   inequality.
6. **Search filters and budget** — `country`, `language` and `recency`
   filters on the free search connectors; a `search_auto` fallback pair
   consumes one research slot, not two.
7. **Metrics 1.1** — source-type and staleness metrics so the benchmark can
   show whether evidence substance moved.

Out of scope: new connectors (Phase 2); research playbooks and model
diversity (Phase 3) beyond the single sentence that tells Finder to fill the
new field; any model-based classification of sources; language detection;
changes to Judge's gate. Nothing here changes how a run is scheduled, mailed
or billed.

## 2. Source typing

### Types

`source_type` is one of:

| Type | Means | Decided by |
|---|---|---|
| `search_snippet` | A search-result excerpt | `kind == "search_snippet"` |
| `forum` | Discussion by users, not sellers | Connector (`hacker_news`, `stack_exchange`, `github`, `local_corpus`) or domain list (reddit, quora, …) |
| `review` | Buyer reviews of products or businesses | Domain list (trustpilot, g2, capterra, app stores, google maps, …) |
| `job_post` | A company paying for this work | Domain and path list (indeed, linkedin.com/jobs, workana, upwork, computrabajo, bumeran, …) |
| `tender` | Public procurement with prices and buyers | Domain list (comprar.gob.ar, ted.europa.eu, sam.gov, compranet, …) |
| `marketplace_listing` | An offer with a price on a marketplace | Domain list (mercadolibre, amazon, ebay, alibaba, etsy, olx, …) |
| `statistics` | Official statistics or trade data | Domain list (indec.gob.ar, inegi.org.mx, ibge.gov.br, eurostat, census.gov, bls.gov, destatis.de, insee.fr, ine.es, data.worldbank.org, comtradeplus.un.org, oecd.org, …) |
| `regulator` | Government or regulator material | TLD and domain rules (`.gov`, `.gob.*`, `.gouv.fr`, `.gc.ca`, `europa.eu`, `.gov.*`, `.mil` excluded) |
| `news` | Editorial reporting | Domain list of major national and business outlets per benchmark region |
| `vendor` | A seller's own commercial page | URL path rules (`/pricing`, `/plans`, `/precios`, `/planes`, `/solutions`, `/soluciones`, `/features`, `/product(s)`, `/producto(s)`, `/demo`, `/case-studies`, `/casos-de-exito`, `/customers`, `/clientes`) |
| `unclassified` | None of the rules matched | Default |

Rules are evaluated in that order; the first match wins, so a `.gob.ar`
procurement portal is a `tender` and a government statistics office is
`statistics`, not `regulator`. `vendor` is deliberately narrow: it names
self-published commercial material with high confidence and leaves the rest
`unclassified`. A trade-association page, a consultancy blog or a company's
home page is `unclassified`, not `vendor`; the gate in §5 treats the two
differently.

### Rules in git

`discovery/source_type_rules.json` (schema `1.0`) holds the lists; a loader
in `discovery/source_types.py` validates it with a Pydantic contract and
exposes `classify(connector, kind, url) -> SourceTypeResult(source_type,
rule, rules_version)`. The file carries `rules_version` (semantic, bumped on
any list change) and a comment per list naming its purpose. Tests assert
the ordering rule above and one example per type. The roadmap's quarterly
review of misclassified evidence edits this file.

### Storage and visibility

- `Evidence.source_type` (char, indexed, default `unclassified`) and
  `Evidence.metadata["source_type_rule"]` /
  `["source_type_rules_version"]`. Evidence rows written after this phase
  carry `schema_version "1.1"`.
- Set at write time in `discovery/research.py` where `origin` is set.
  Connectors stay unaware of source types; the research service classifies.
- Backfill: `manage.py classify_evidence [--apply]` recomputes
  `source_type` for rows whose `source_type_rules_version` differs from the
  current one, dry-run by default, and reports the change counts per type.
  It is also how a rules bump is applied to history. Backfilled rows keep
  their `schema_version`; the metadata records which rules classified them.
- Tool results (`AgentTools.model_result`), the role context
  (`loop._initial_context["evidence"]`), the admin evidence table and the
  report's Source register all show `source_type`, `origin` and
  `published_at`.

## 3. Freshness

`fetch_public_url` sets `ResearchDocument.published_at` when a page states
it, trying in order: `<meta property="article:published_time">`,
`<meta name="date">` / `dc.date` / `pubdate`, JSON-LD `datePublished`
(first `NewsArticle`/`Article`/`WebPage`), `<time datetime>` with
`pubdate` or `itemprop="datePublished"`, then the HTTP `Last-Modified`
header. The source of the date is recorded in
`metadata["published_at_source"]`. Dates must parse as ISO-8601 or RFC-2822
and must not be in the future beyond one day; otherwise they are ignored
and nothing is recorded. For PDFs, `pdfinfo`'s `CreationDate` or `ModDate`
is used with `published_at_source` `pdf_creation`/`pdf_modified`.
`Last-Modified` is the weakest signal and is labelled as such everywhere it
is shown. No `published_at` is still a valid outcome.

The report's Source register prints the date and its source, and the
Evidence quality section prints the share with a known date (already in
metrics 1.0). The Limitations section lists cited evidence older than
`STALE_EVIDENCE_MONTHS = 24` at retrieval time with its age, as an
observation; staleness never blocks acceptance in this phase.

## 4. Business-model kind on the Proposal

`Proposal` contract `1.0 → 1.1` adds
`business_model_kind: Literal["services", "manufacturing", "goods",
"intelligence", "software", "marketplace", "other"]`, required. The
free-text `business_model` stays. The Finder tool schema is generated from
the contract, so the field appears automatically; `prompts/finder/system.md`
gains one sentence naming it (prompt `1.1 → 1.2`). Proposals stored under
contract 1.0 read as kind `unspecified` wherever the kind is consumed;
nothing is rewritten.

Why structured: the typed gate (§5) and the Phase 0 benchmark's
`business_model_focus` both need a comparable value, and the Judge's
`objective_fit` reasoning can point at a mismatch instead of inferring one.

## 5. Evidence policy and independence

### Config 1.3

`DiscoveryConfig` `1.2 → 1.3` adds:

| Field | Default | Meaning |
|---|---|---|
| `evidence_policy` | `"origins"` | `origins` = today's rule; `typed` = the rule below |
| `min_validator_query_novelty` | `0.0` | Minimum `1 − max Jaccard` between a Validator search and every earlier Finder search for that search to count as independent; `0.0` keeps today's behaviour (any non-identical query) |

Both are frozen in `config_snapshot` like every other setting and shown in
the start form and the report configuration block. Explicit 1.0–1.2
snapshots read as `origins` and `0.0`. Changing the deployment default is a
separate decision recorded in `docs/discovery-eval.md` with its benchmark
row (§8).

### Typed policy

With `evidence_policy == "typed"`, `evidence_gaps()` keeps every current
check and adds, over the final proposal's cited evidence:

1. **Independent demand.** `demand_evidence_ids` must include at least one
   row whose `source_type` is in `INDEPENDENT_DEMAND_TYPES = {forum, review,
   job_post, tender, regulator, statistics, news, marketplace_listing}`.
   `vendor`, `search_snippet` and `unclassified` do not satisfy it. Error:
   `"Demand evidence needs one independent source (forum, review, job post,
   tender, regulator, statistics, news or marketplace listing); cited demand
   sources are: <id: type, …>"`.
2. **Offer-level competition** when `business_model_kind` is `services`,
   `manufacturing`, `goods` or `marketplace`: `competitor_evidence_ids` must
   include at least one row whose `source_type` is in
   `OFFER_TYPES = {vendor, marketplace_listing, job_post, tender, review}`,
   that is, a page about an actual supplier, offer or price rather than
   commentary. Error names the cited competitor sources and their types.
3. Proposals of kind `unspecified` (stored before this phase) are checked
   with rule 1 only.

The gate still runs where it runs today: at Validator acceptance in
`require_agreement`, so a failure becomes a Validator challenge that tells
Finder which type is missing. The `recovery_hint` in `agent_tools.py` is
keyed today on three exact message strings; it becomes a flag on
`SubmissionError` (`finder_must_revise=True`) so the new messages carry the
hint without string matching. The hint text names the missing type and the
enabled connectors most likely to provide it.

### Independence

`has_independent_research()` replaces the string-inequality test with the
Phase 0 `_novelty` helper (moved to `discovery/independence.py`, imported by
both `quality.py` and `termination.py`): a Validator search counts when its
novelty against every earlier Finder query is at least
`min_validator_query_novelty` and it succeeded with documents. The error
when none qualifies reports the best novelty achieved and the threshold, so
Validator can see that rewording is not enough. The tokenizer stays the 1.0
one (casefold, split on non-word characters, no stop words); a stop-word
list is a later, versioned change.

## 6. Search filters and budget

### Filters

`ResearchInput.filters` (in `discovery/research.py`) is a free dict validated
per connector by `validate_search` against `supported_filters`. This phase adds three
filter names and declares them where a backend can honour them:

| Filter | Values | DuckDuckGo | Bing | `search_auto` | Tavily |
|---|---|---|---|---|---|
| `country` | ISO 3166-1 alpha-2, lower case | `kl=<country>-<language>` (needs `language`; defaults from a small country→language table) | `cc=<CC>` | both | unchanged |
| `language` | ISO 639-1 | part of `kl` | `setlang=<lang>` | both | unchanged |
| `recency` | `day`, `week`, `month`, `year` | `df=d/w/m/y` | not supported | DuckDuckGo only | unchanged |

`search_auto` declares all three. When it falls back to Bing with `recency`
set, Bing runs without it and the attempt record in `search_attempts` gains
`filters_applied` and `filters_dropped: ["recency"]`; the response's top
level repeats both. Nothing is dropped silently, and a caller who needs
recency can see it was not honoured. A filter value outside the allowed set
is a `validation_failed` tool result, as today.

The research tool description and the `filters` field description in
`ResearchInput` list the filter names and values so the roles know they
exist; the connector descriptions already enumerate `supported_filters`.

### Budget

`research_calls_used` counts a `search_auto` request as one slot: operations
whose `operation_key` ends in `:backend:<id>` are grouped under their parent
key and the group counts once. The pre-check in the fallback loop that
compared the used count with the limit is removed; `check_limits` inside
`_perform_research` remains and is now correct for the pair. The research
tool's `research_calls_remaining` reflects the grouped count.

## 7. Metrics 1.1 and report

`QualityMetrics` `1.0 → 1.1` adds:

| Metric | Definition |
|---|---|
| `cited_source_type_counts` | Map `source_type` → cited rows |
| `demand_independent_source_share` | Demand citations with type in `INDEPENDENT_DEMAND_TYPES` ÷ demand citations |
| `competitor_offer_source_share` | Competitor citations with type in `OFFER_TYPES` ÷ competitor citations |
| `stale_cited_share` | Cited rows older than 24 months at retrieval ÷ cited rows with a known date; `None` when none is dated |
| `business_model_kind` | From the final proposal, `unspecified` for 1.0 proposals |
| `benchmark_focus_match` | `True` when the kind is in the benchmark objective's `business_model_focus`; `None` off-benchmark |
| `evidence_policy`, `min_validator_query_novelty` | From the frozen config, so runs under different policies are never averaged unknowingly |

`score_discoveries --compare-to` already warns on a metrics schema change;
the eval doc says which 1.1 metrics have no 1.0 counterpart. The report's
Evidence quality section prints the type counts and the two shares; the
Source register prints `type`, `published <date> (<source>)` and age.

## 8. Rollout and the default flip

1. Ship §2–§7 with `evidence_policy="origins"` and
   `min_validator_query_novelty=0.0`. Behaviour of existing and new runs is
   unchanged except for richer tool results, context and reports.
2. Run the Phase 0 baseline if it has not been run (default policy).
3. Run the benchmark again under tag `phase1-typed-<yyyy-mm>` with
   `--config-json '{"evidence_policy": "typed",
   "min_validator_query_novelty": 0.4}'`; record human verdicts; compare.
4. Decide from the comparison: if `demand_independent_source_share` and
   `competitor_offer_source_share` rise and the share of
   `insufficient_evidence` outcomes and cost per approved proposal stay
   within what the reviewer accepts, change the deployment defaults in a
   dedicated commit that cites the two quality-run IDs in its message and
   adds the row to `docs/discovery-eval.md`. The threshold `0.4` is the
   starting proposal; the baseline's novelty distribution may move it.

## 9. Acceptance scenarios

1. A fetched `https://www.indec.gob.ar/...` page is `statistics`; a
   `https://comprar.gob.ar/...` page is `tender`; `https://acme.com/pricing`
   is `vendor`; `https://acme.com/about` is `unclassified`; a Hacker News
   item is `forum`; a Bing result is `search_snippet`. Each carries the rule
   name and rules version in metadata and shows the type in the tool result.
2. `classify_evidence` on a database with pre-phase rows reports per-type
   counts in dry run and writes nothing; `--apply` updates `source_type` and
   metadata and a second `--apply` changes nothing.
3. A page with `article:published_time` yields that date with source
   `meta_article_published`; a page with only `Last-Modified` yields that
   with source `http_last_modified`; a page with a future date yields no
   `published_at`; a PDF yields its `CreationDate`.
4. A report whose final proposal cites a page published 30 months before
   retrieval lists it under Limitations with its age; acceptance was not
   blocked.
5. Under `origins`, the Phase 0 smoke fixtures converge exactly as before.
   Under `typed`, a proposal whose demand evidence is a vendor pricing page
   and a search snippet is refused at Validator acceptance with the
   independent-demand message naming both IDs and types, and the tool
   result carries the recovery hint; adding a forum thread as demand
   evidence makes it pass.
6. Under `typed`, a `services` proposal whose competitor evidence is one
   news article is refused with the offer-level message; a `software`
   proposal with the same evidence passes rule 2 (it is not a physical or
   services business) and is judged by rule 1 only.
7. With `min_validator_query_novelty=0.4`, a Validator whose only search is
   Finder's query with one word changed cannot accept, and the error states
   the best novelty achieved; a distinct query (novelty ≥ 0.4) can.
8. `search_auto` with `{"country": "ar", "language": "es", "recency":
   "month"}` sends `kl=ar-es&df=m` to DuckDuckGo; on fallback Bing receives
   `cc=AR&setlang=es` and the attempt record shows `filters_dropped:
   ["recency"]`.
9. A turn with `max_research_calls_per_turn=8` can make eight `search_auto`
   searches that each fall back to Bing; the ninth is refused with the usual
   research-limit message.
10. `compute_quality` on a typed-policy fixture returns the 1.1 metrics;
    `score_discoveries --compare-to` a 1.0 run warns about the schema change.
11. A Finder proposal without `business_model_kind` is rejected by the tool
    with a validation message; a 1.0 proposal stored earlier renders and
    scores as `unspecified`.

## 10. Compatibility and decisions

- Additive storage: `source_type` defaults to `unclassified`; `published_at`
  was already nullable. Historical evidence is reclassified only by the
  explicit backfill command.
- Contracts bumped: Evidence rows `1.1`, Proposal `1.1`, DiscoveryConfig
  `1.3`, QualityMetrics `1.1`, research response stays `1.1` with additive
  `filters_applied`/`filters_dropped`, Finder prompt `1.2`. Each bump lands
  in the same commit as its migration or reader change.
- The typed gate is strict on purpose and off by default. Measuring the
  trade-off is the point of pairing it with the Phase 0 benchmark; flipping
  the default is its own commit with evidence.
- `vendor` is detected by URL path only. A seller's home page is
  `unclassified`, which already fails the independent-demand rule; the
  narrow `vendor` type exists so a competitor's pricing page can count as
  offer-level competitor evidence.
- Classification lives in the research service, not in connectors, which
  stay read-only data providers, and not in the model, which must not grade
  its own evidence.
- Independence uses the same helper as the metric so the number the gate
  enforces is the number the report shows.
- Recency on Bing is dropped with an audit record rather than refusing the
  fallback, because a search without a date filter is still useful and the
  record keeps the degradation visible.
