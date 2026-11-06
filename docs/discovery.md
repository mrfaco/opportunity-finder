# Finder–Validator–Judge discovery guide

## What a discovery does

Start from a sector or problem objective instead of waiting for an ingested
source to produce a cluster. Finder searches and proposes a concrete business.
Validator independently looks for evidence against it and sends objections or
alternative directions through internal durable mail. Finder can defend, narrow,
pivot or abandon a proposal. Both agents consider services, manufacturing,
intelligence, physical goods, software and other business models across B2B,
B2C and mixed customer relationships.

The default limit is 100 individual submissions shared by Finder, Validator and
Judge. Research/model calls inside a turn have separate limits. Validator accepts
Finder's exact immutable proposal revision after evidence checks; the agreement
is then mailed to Judge. Only Judge approval completes a new run as converged.
Validator's suggested niche must first be adopted by Finder and reviewed again.

Judge defaults to Ollama `kimi-k3:cloud` in a fresh Claude Code session, with its
own frozen provider/model and usage accounting. It examines economics after
overhead and founder effort, opportunity cost, observed buying transactions,
competitive pressure, fit to the original objective, and convergence by exhaustion.
Honest reporting and correct arithmetic alone do not make an opportunity worthwhile.

Judge can approve, request revision, or reject/no-go. A revision returns to Finder
and then Validator; a no-go requires a materially different opportunity within the
original objective. The rejection and its reasons remain in context and the report.
The agents cannot reset turn/time/spending limits after a rejection. If no approved
opportunity is found before a limit, the run ends with a partial report, not approval.
Approval is a research judgment, not proof of willingness to pay.

Objections are scoped to the business they challenge. A cited Finder pivot or
abandonment archives the named prior-business objections; a new opportunity
after Judge no-go archives that rejected business's objection set. Archival
does not claim the old objections were resolved: immutable reviews and rejection
history keep them intact. Repeating their stable IDs later does not resurrect
requirements to prove an abandoned business viable. New-business objections use
new IDs and still block approval until properly addressed.

The report's main evidence section uses the final proposal, its latest
Validator review and current Judge verdict. Repeated claims and citations are
collapsed. Next human tests
use the latest review of the exact proposal when it supplies a checklist,
otherwise the final proposal. A current Judge checklist takes precedence. Tests
from abandoned directions remain under their original turns in the history.

Completion distinguishes convergence, continued disagreement and insufficient
evidence. Failed or cancelled discoveries preserve partial findings. A report
states the actual stop reason and remaining objections. Agreement is research
consensus between agents, not verified demand, profitability or legal clearance.

## Runtime and credentials

All reasoning roles run as **Claude Code subprocesses**, following Video
Swarm's agent-harness pattern. The application owns durable tasks, internal
mail, tools, budgets and report generation; Claude Code owns each role's model
conversation. In the start form, choose a provider and model separately for
Finder and Validator:

| Provider | Model ID example | Host configuration |
|---|---|---|
| OpenRouter | `deepseek/deepseek-v4.1-flash` | `OPENROUTER_API_KEY` |
| Anthropic | `sonnet` or a native Claude model ID | Host Claude Code login or `ANTHROPIC_API_KEY` |
| Ollama | An exact installed/cloud Ollama model tag | `ollama` executable and configured Ollama service |

Prefer a different model family for Validator to reduce shared blind spots.
Providers and models can be selected independently. Separate sessions of the same
model do not supply model diversity; reports flag identical selections. Existing
run snapshots retain the original choices.

All three choices use Claude Code; this is not three different reasoning loops.
The default for new discoveries is OpenRouter with interactive DeepSeek V4.1
Flash. Provider credentials are configured on the host and never submitted as
form fields or saved in a discovery snapshot. Claude's built-in filesystem,
shell and unrelated integrations are not part of the research toolset.

**Batch suffixes are not accepted for Claude Code discoveries.** A dependent
interactive tool conversation needs a model response before research and the
next handoff can proceed. Use `deepseek/deepseek-v4.1-flash` rather than its
`:batch` variant. The application rejects that mismatch explicitly; it never
silently removes the suffix or switches a provider. Existing schema-1.0 batch
records retain their historical execution configuration and remain inspectable.

Configuration schema `1.1` added `runtime`, `finder_provider` and
`validator_provider`. New schema `1.2` adds `judge_enabled`, `judge_provider` and
`judge_model`, defaulting to enabled, Ollama and `kimi-k3:cloud`. Explicit schema
1.0/1.1 configurations keep Judge disabled unless explicitly configured; stored
historical records are not rewritten. Legacy batch execution cannot enable Judge.
Old schema-1.0 snapshots without `runtime` are interpreted as `openrouter_batch`.
All role selections,
connectors, maximum turns, cost ceilings and call limits are frozen when a new
discovery starts. Changing deployment defaults affects subsequent discoveries.

Role providers can differ: for example, Finder on Ollama and Validator on
Anthropic. Choose model IDs appropriate to each provider. Do not place
credentials in objectives, constraints, briefs, evidence or committed files.

General search is explicitly configured:

| Connector | Configuration / purpose |
|---|---|
| `web_search` | Tavily general search; requires `TAVILY_API_KEY` |
| `brave` | Brave Search API general search (opt-in); requires `BRAVE_SEARCH_API_KEY`; filters `country`, `language`, `recency`; cursor |
| `web_fetch` | Bounded HTML/text and page-addressable PDF retrieval; Poppler required |
| `duckduckgo` | DuckDuckGo HTML general search |
| `bing` | Independent Bing RSS general search; no API key |
| `search_auto` | Explicit DuckDuckGo → Bing fallback on rate limit, timeout, denial or source error |
| `hacker_news` | Search/fetch public Hacker News material |
| `github` | GitHub public research with `GITHUB_TOKEN` |
| `stack_exchange` | Public Stack Exchange research; optional `STACKEXCHANGE_KEY` |
| `jobs_adzuna` | Adzuna job adverts with salaries (opt-in); requires `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`; filters `country` (required), `location`, `recency`; cursor |
| `local_corpus` | Read-only search of existing local cluster/item records |
| `news_gdelt` | GDELT DOC 2.0 dated news (no key); filters `country`, `language`, `recency`; publisher URLs to fetch with `web_fetch` |
| `open_data_ckan` | Government datasets from the `DISCOVERY_CKAN_PORTALS` allowlist (no key); filters `portal`, `format`; cursor |
| `reddit` | Reddit posts and threads (public JSON; optional `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` OAuth); filters `subreddit`, `recency`, `sort`; cursor |
| `wayback` | Internet Archive captures of a URL or host (no key); filters `from_year`, `to_year`, `match`; snapshots typed as the archived site |

New runs default to
`DISCOVERY_CONNECTORS=search_auto,web_fetch,reddit,news_gdelt,open_data_ckan,wayback`:
general search plus the keyless sources that show buying behaviour (roadmap Phase 2).
Only keyless connectors are defaults, because `start_discovery` preflights every selected
connector and a missing key fails the start visibly. Keyed connectors (`web_search` with
`TAVILY_API_KEY`, `brave` with `BRAVE_SEARCH_API_KEY`, `jobs_adzuna` with
`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, `github` with `GITHUB_TOKEN`) are explicit selections.
Existing discoveries keep the connector list frozen in their snapshot. Keys are read from
settings inside each connector; they are never accepted from tool input, and connector
descriptions report only whether a key is configured. Each connector's
`access_limitations` states in one sentence what the source proves and what it does not;
it is the only text the roles see about a source. Connectors use documented provider
APIs and endpoints only: no HTML scraping of job boards, review sites or marketplaces.
`search_auto` makes at most one attempt at each backend; every attempt has its own
operation key, evidence provenance and cooldown, and the DuckDuckGo → Bing pair uses
one research slot (operations keyed `<parent>:backend:<id>` count once under their
parent, also when older turns are recounted). The tool response
uses schema 1.1 with `requested_connector` and `search_attempts`; existing schema-1.0
single-backend responses remain supported. Empty successful results do not trigger
fallback. Local cooldown denials remain audited without spending a source-call slot.
No challenge is bypassed.

Search filters: `country` (ISO 3166-1 alpha-2, lower case) and `language` (ISO 639-1)
map to DuckDuckGo `kl=<country>-<language>` (a country alone takes its main language
from a small table; a language needs a country) and Bing `cc`/`setlang`; `recency`
(`day`, `week`, `month`, `year`) maps to DuckDuckGo `df`. Bing cannot honour recency:
on fallback it runs without it, and the attempt record and the response's top level
show `filters_applied` and `filters_dropped: ["recency"]`. Invalid values are a
`validation_failed` tool result before any request.
Connector availability and settings appear at `/api/v1/connectors/` and the start form.
Existing stored connector selections and run histories are unchanged.

`web_fetch` connector version 1.1 adds PDF extraction via Poppler (`brew install poppler`
on macOS; `poppler-utils` is installed in Docker). PDFs are bounded to 20 MB and ten
pages / 120 KB extracted text per call, with subprocess timeouts. Use a URL fragment
`#page=21` or `#page=21-30` to retrieve a later page window. Each readable page is an
independently citable evidence record with its page URL, page count, source-file SHA256,
extraction method and next-page link. These metadata are visible to the model. The
ResearchDocument schema remains 1.0 because page locators live in the existing metadata.
HTML captures also expose up to 30 linked PDF URLs in metadata, so agents can open
annexes linked by a regulator. Original public-IP checks, pinned DNS, TLS verification
and redirect validation apply.
Scanned pages with no text return an explicit OCR-needed outcome; corrupt, encrypted,
oversized and timed-out files are visible errors, never successful empty evidence.
HTML/text keeps the 200 KB bound. A source 429 gets a bounded cooldown (numeric
Retry-After is honored), scoped to its host, so a datospublicos limit cannot block
unrelated government/regulator sites.

`web_fetch` connector version 1.2 also reads CSV (`text/csv`, `application/csv`, or a
`.csv` path served as `application/octet-stream`) under the same 200 KB bound, so an
open-data resource is readable evidence. The capture's metadata carries
`format: "csv"` and `columns` from the header row; a larger file is truncated and
flagged like any other text capture. Other octet-stream bodies are still refused.

API connectors share `discovery/connectors/http.py` (`get_json`/`post_json`): fixed
provider hosts only, an identified `User-Agent`, a 20-second timeout, no redirects, a
2 MB response cap, and a typed `ResearchPage` for every provider failure (transport
error or timeout, 401/403, 404, 429 with `Retry-After`, redirect, other non-2xx,
non-JSON or oversized body). It never fetches a role-chosen URL; `fetch_public_url`
remains the only path for those.

`reddit` searches `/search.json` (or `/r/<subreddit>/search.json` with `restrict_sr=1`)
with `recency` → `t`, `sort` (`relevance`, `top`, `new`, `comments`) and the `after`
cursor. A self post with a body is a `source_record` (typed `forum`); a link post is a
`search_snippet` with `link_url` in metadata. Fetch takes a post ID or reddit.com
permalink and returns the post with its top 40 top-level comments by score, each
prefixed with author and score. Without credentials it calls `www.reddit.com` at most
every 6 s; Reddit often denies cloud IPs, which is a visible `access_denied` telling the
operator to configure OAuth. With both `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` set
it obtains a client-credentials token (cached in the Django cache for its lifetime minus
a minute, keyed by a hash of the client ID) and calls `oauth.reddit.com` at most every
second. Setting only one of the two is a configuration error.

`news_gdelt` queries the GDELT DOC 2.0 API (`mode=artlist`, `format=json`,
`maxrecords` = limit) at most every 5 s. `country` becomes `sourcecountry:<FIPS code>`
(GDELT uses FIPS 10-4, so Germany is `GM` and Spain `SP`; only countries in the
connector's table are accepted), `language` becomes `sourcelang:<ISO 639-3>` for
`de en es fr hi it nl pl pt`, and `recency` becomes `timespan` (`1d`, `1w`, `1m`, `1y`;
default `3m`). Each article is a `search_snippet` whose URL is the publisher's and whose
`published_at` is GDELT's `seendate`; the role fetches the article with `web_fetch`, so
the evidence record is typed by the publisher domain. No key; GDELT lags about 15
minutes and covers small non-English outlets thinly.

`open_data_ckan` searches one CKAN portal from `DISCOVERY_CKAN_PORTALS` (API base URLs;
default `datos.gob.ar`, `datos.gob.mx/busca`, `www.govdata.de/ckan`,
`catalog.data.gov`; Brazil's and Spain's national portals run their own APIs, not
CKAN, and are not listed) through `/api/3/action/package_search`
(`q`, `rows` = limit, `start` = cursor, `fq=res_format:<FORMAT>` for `format` `CSV`,
`JSON`, `XLSX` or `PDF`). The `portal` filter is the portal host without `www.` and is
required when more than one portal is configured, so a search never fans out across
governments silently. Each dataset is a `source_record`: notes (up to 4,000
characters) as text, `metadata_modified` as `published_at`, organization, tags and up to
ten resources (`name`, `format`, `url`, `size`) in `metadata["resources"]`, so the role
can `web_fetch` a CSV or PDF directly. Fetch takes `<portal>:<dataset id>` or a
configured portal's `/dataset/<id>` URL and returns all resources via `package_show`.
A malformed allowlist entry makes the connector unconfigured (a preflight failure);
portal outages are typed errors naming the portal.

`wayback` looks up a URL or host (not keywords) in the Wayback CDX index
(`output=json`, `filter=statuscode:200`, `collapse=timestamp:6` for at most one capture
a month, `limit`, optional `from`/`to` years and `matchType` `exact` or `prefix`). Each
capture is a `source_record` whose URL is the raw snapshot
(`https://web.archive.org/web/<timestamp>id_/<original>`), `published_at` is the capture
time and metadata carries `original_url`, `canonical_url`, `original_source_origin`,
`mimetype` and `statuscode`. No captures is a success with no documents and a note that
absence from the archive is not absence from the web. Fetch takes a snapshot URL,
retrieves its `id_` form through `fetch_public_url` and relabels the capture with
`canonical_url` and `original_source_origin` set to the archived original (and the
capture actually served, when the archive redirects to the nearest one), so the
evidence row has the original's `origin` and `source_type` and counts as one origin with
the live page. A capture without a stated page date takes the capture time as
`published_at` (`published_at_source: wayback_capture`). Interval 2 s; the archive's own
rate limiting is a `rate_limited` outcome.

`brave` is an explicit, keyed general search: it satisfies the "general web search"
requirement of a discovery config, but `search_auto` stays DuckDuckGo → Bing, so no
key-gated backend is ever reached by fallback. It calls
`api.search.brave.com/res/v1/web/search` with the `X-Subscription-Token` header from
`BRAVE_SEARCH_API_KEY`, `count` = limit, `offset` = cursor (Brave pages by whole result
pages, 0–9), `country` upper-cased, `search_lang` (`pt` becomes `pt-br`, or `pt-pt`
outside Brazil) and `freshness` from `recency` (`pd`, `pw`, `pm`, `py`). Results are
`search_snippet`s dated from `page_age` (or an absolute `age`). Each search records
`DISCOVERY_BRAVE_SEARCH_COST_USD` (default `0`, the free tier) as an estimated charge,
like Tavily. Selecting `brave` without the key fails `start_discovery` preflight with
the key named.

`jobs_adzuna` calls `api.adzuna.com/v1/api/jobs/<country>/search/<page>` with
`app_id`/`app_key` from settings, `what` = query, `where` = `location`,
`results_per_page` = limit, `max_days_old` from `recency` (1, 7, 30, 365) and
`sort_by=date`; the cursor is the page number. `country` is required and must be an
Adzuna market (`at au be br ca ch de es fr gb in it mx nl nz pl sg us za`); `ar` is
refused with a pointer to `jobs_jooble` (Tier 2b). Each advert is a `source_record`
(typed `job_post` by the Adzuna host patterns) with the Adzuna redirect URL, the
employer as author, the description (up to 3,000 characters), `created` as
`published_at`, and company, location, `salary_min`, `salary_max`,
`salary_is_predicted`, `currency` (the market's own; Adzuna does not return one),
`contract_time`, `contract_type` and category in metadata. Keys travel only in the
request; tool results, evidence and errors never carry them.

Search excerpts are leads. Decisive claims must cite captured page/source
material, and acceptance requires demand and competitor evidence from distinct
source origins, with independent Validator research. Cached material retains
its original retrieval timestamp.

Every evidence row carries a deterministic `source_type` (`search_snippet`,
`forum`, `review`, `job_post`, `tender`, `marketplace_listing`, `statistics`,
`regulator`, `news`, `vendor` or `unclassified`). The research service decides
it at write time from the connector, document kind, hostname and URL path using
the versioned lists in `discovery/source_type_rules.json`; the first matching
type wins in that order (statistics precedes regulator so a government
statistics office is not a regulator). `vendor` is URL-path only (`/pricing`,
`/precios`, `/products`, …); a seller's home page stays `unclassified`. The rule
and rules version are in the row's metadata, and new rows carry evidence schema
1.1. Tool results, role context, the admin evidence list and the report's Source
register show `source_type`, `origin` and `published_at`. A connector that returns a proxied capture
(a web-archive snapshot) sets `metadata["canonical_url"]` to the archived original;
the row is then typed by that URL and its `origin` is that URL's host, and
`metadata["original_source_origin"]` makes the snapshot and the live page count as
one origin. Rules 1.1.0 add Adzuna (`adzuna.*`, `adzuna.co.*`, `adzuna.com.*` host
patterns) and `jooble.org` as job posts. After editing the
rules file, bump `rules_version` and run `manage.py classify_evidence` (dry run)
then `manage.py classify_evidence --apply` to reclassify history; the command
only touches rows classified under a different rules version and is idempotent.

Discovery config schema 1.3 adds two frozen settings, shown in the start form
and the report. `evidence_policy` is `origins` (default: demand and competitor
evidence from two distinct non-snippet origins) or `typed`, which also requires
at Validator acceptance one demand source of an independent type (forum,
review, job post, tender, regulator, statistics, news or marketplace listing)
and, when the proposal's `business_model_kind` is services, manufacturing,
goods or marketplace, one offer-level competitor source (vendor page,
marketplace listing, job post, tender or review). Proposals stored under
contract 1.0 are checked for independent demand only. A failed check is
flagged `finder_must_revise` and the Validator tool result carries a recovery
hint naming the missing types and the enabled connectors likely to supply
them. `min_validator_query_novelty` (default 0.0) is the minimum
`1 − max Jaccard` word overlap between an accepting Validator search and every
earlier Finder search, computed by `discovery/independence.py`, the same helper
as the quality metric; 0.0 keeps the earlier rule that any non-identical query
counts, and the refusal reports the best novelty achieved. Explicit 1.0–1.2
configs read as `origins` and 0.0 and cannot set other values.

## Admin

- `/admin/discovery/discovery/`: list and filter discoveries.
- `/admin/discovery/discovery/start/`: start from an objective, constraints,
  selected connectors, role providers/models and budgets.
- `/admin/discovery/discovery/<id>/review/`: inspect status, costs, threaded mail,
  immutable submissions, evidence and provider-operation status. Terminal
  discoveries show an evidence-quality metrics table above the report; see
  `docs/discovery-eval.md`.
- The detail page provides cancellation and final Markdown report download.
- Once a discovery is completed, failed or cancelled, a user with change
  permission can record a human verdict (`good`, `weak`, `wrong`, `untested`;
  `wrong` requires notes) at the top of the review page. Verdicts are
  append-only: the newest is current and earlier ones remain listed. Each
  verdict records the final proposal and Judge verdict current at the time,
  and recording one regenerates the stored report.

Source/model text is escaped in the interface. Reading the operator page does
not mark an agent's assigned task complete. Refresh the detail page to inspect
persisted progress while Claude Code researches and prepares a handoff.

## API

The API uses the existing `Authorization: Bearer <application-api-key>`
authentication. This is an application key created through the API-key admin,
not the OpenRouter provider key. All endpoints require authentication.

The request envelope remains version 1.0, configuration is version 1.2, and
discovery/report responses are version 1.2. Version 1.2 adds `human_verdict`
(the latest verdict or null), `verdict_count`, `benchmark_tag` and
`benchmark_objective_id`. The detail response (`GET /api/v1/discoveries/<id>/`)
also carries `quality`, the `QualityMetrics` 1.0 object for terminal
discoveries and null while running; list responses omit it. Stored 1.1
`report_json` is not rewritten; `regenerate_report` produces the 1.2 shape,
which adds `quality` and `human_verdict`. Responses include Judge execution
settings, the latest verdict and judgment history. Judge approval is tied to
the exact latest proposal and accepted validation; stale approval is not reused.

Start with a stable idempotency key. Reusing it with the same request returns
the same discovery; a different request conflicts rather than starting another
paid run.

```http
POST /api/v1/discoveries/
Authorization: Bearer <application-api-key>
Idempotency-Key: travel-research-001
Content-Type: application/json

{
  "schema_version": "1.0",
  "objective": "Look for opportunities in the travel space",
  "constraints": "Argentina; include service and intelligence businesses",
  "config": {
    "schema_version": "1.2",
    "runtime": "claude_code",
    "finder_provider": "openrouter",
    "validator_provider": "anthropic",
    "max_turns": 100,
    "max_cost_usd": "10.00",
    "max_duration_s": 86400,
    "finder_model": "deepseek/deepseek-v4.1-flash",
    "validator_model": "sonnet",
    "judge_enabled": true,
    "judge_provider": "ollama",
    "judge_model": "kimi-k3:cloud",
    "connectors": ["search_auto", "web_fetch"]
  }
}
```

Other endpoints:

| Method/path | Result |
|---|---|
| `GET /api/v1/discoveries/` | Newest discoveries; optional `status`, `limit`, `offset` |
| `GET /api/v1/discoveries/<id>/` | State, outcome, counters, safe frozen config and report readiness |
| `GET /api/v1/discoveries/<id>/messages/` | Paginated internal mail, oldest first |
| `GET /api/v1/discoveries/<id>/evidence/` | Paginated captured evidence |
| `GET /api/v1/discoveries/<id>/report/` | Markdown attachment; 409 until ready |
| `POST /api/v1/discoveries/<id>/cancel/` | Idempotent cancellation |
| `POST /api/v1/discoveries/<id>/verdict/` | Record a human verdict `{"schema_version": "1.0", "verdict": "good", "notes": "..."}`; 201 with the record, 409 unless terminal, 400 on contract errors. `recorded_by` is the API key's owner |
| `GET /api/v1/connectors/` | Capability and safe configuration status |

Paginated responses contain `count`, `limit`, `offset`, `next_offset` and
`results`. Limits are 1–200. An omitted limit defaults to 50. Report downloads
are Markdown text rather than executable HTML. Cancellation prevents new calls;
work already submitted may still incur a charge.

## Benchmark runs

`discovery/eval/objectives.json` is the versioned benchmark set (twelve
objectives with a reviewer rubric each). `manage.py run_discovery_benchmark`
starts one discovery per objective through the normal idempotent start
service with key `benchmark:<tag>:<objective_id>`, and records
`benchmark_tag` and `benchmark_objective_id` on each discovery.

```sh
python manage.py run_discovery_benchmark --tag baseline-2026-11            # dry run: prints plan
python manage.py run_discovery_benchmark --tag baseline-2026-11 \
    --authorize-spend --max-total-usd 120                                   # starts discoveries
```

- Without `--authorize-spend` nothing starts; the command prints the frozen
  config, role prompt hashes, the ceiling sum and each idempotency key.
- `--max-total-usd` is required to start and the command refuses when
  `max_cost_usd × objectives` exceeds it.
- `--objective <id>` (repeatable) runs a subset; `--config-json '{...}'` is
  merged over deployment defaults and validated before anything starts.
- Re-running with the same tag never starts a second discovery for an
  existing key. The command does not wait; workers run the discoveries.
- The review page shows the objective's rubric beside the verdict form.

`manage.py score_discoveries --tag <tag>` (or `--discovery <id>`, repeatable;
optional `--since YYYY-MM-DD`) computes the quality metrics for every terminal
discovery selected, lists skipped non-terminal ones and persists one
append-only `DiscoveryQualityRun` with per-metric mean, median, min, max, sum,
count and `None` count. `--compare-to <run id>` prints per-metric deltas and
warns when prompt hashes, configurations or the metrics schema differ.
`--format json` prints the persisted run.

## Recovery and billing

Messages, assignments, completed turns and exact proposal references are
persisted. Reading mail is not task completion. Transactional dispatch records
allow committed tasks to be published after temporary broker failures. Leases
and fencing prevent a stale worker from committing another handoff.

Acceptance failures on an immutable proposal's evidence set must become a
Validator challenge, so Finder can revise the proposal. Adding citations only
to a Validator review does not repair Finder's sources. The tool reports this
recovery direction instead of encouraging repeated acceptance attempts.
Unknown citation errors include full captured-source ID/title/URL candidates
when a prefix matches exactly one source. These are correction hints: unknown
IDs are still rejected, and no citation is silently substituted.

An operator can explicitly authorize a bounded correction of a settled wake
with a UUID `claude_recovery_id` in task context. It opens a distinct ledger/log
identity, preserves the original logs and allows at most two model calls. Prior
unsettled model outcomes block it; reusing the token cannot replay the wake.
This is not an automatic retry or a reset of turn, time or spending limits.
Research requests denied locally during a shared connector cooldown retain
their audit records but do not consume a source-call slot. Actual outbound
requests, including provider-side rate limits, and cached research results still
count. Local-denial responses use schema 1.1 with `request_performed=false`;
historical local denials are recognized by their existing error reason.
For a proven pre-inference executable launch failure, `claude_recovery_mode`
may be `launch_failure`: it restores the original call allowance only when a
prior failed wake records `launch_failed_before_model=true`. Empty output and
an explicit exec failure must establish that no inference started; absent such
proof the billing outcome remains unknown and blocks retries.

External operation records distinguish planned, submitted, completed and unknown
outcomes. A Claude Code process interrupted after inference may have incurred a
charge; do not blindly start a second billed attempt. Historical batch jobs use
their saved provider ID when polling rather than creating a second batch. Unknown billed
outcomes require operator reconciliation before another paid attempt. Known
spend and unknown outcomes remain distinct in the final report. Do not infer
zero spend from missing usage.

Inspect discovery detail and worker logs for the error and operation IDs. Keep
provider identifiers and evidence when investigating. A report-render failure
can be retried from persisted records via
`discovery.orchestrator.regenerate_report(discovery_id)` without another model
call. Cancellation cannot recover money already billed by a provider.

## Adding a connector

Implement the `Connector` interface in `discovery/connectors/base.py`:

- `describe()` returns a versioned `ConnectorDescription` with stable ID,
  capabilities, supported filters, safe configuration status and cost bounds.
- `search(query, filters, cursor, limit)` returns a normalized `ResearchPage`.
- `fetch(target)` returns captured source documents where supported.
- `check_configuration()` verifies locally that required settings exist.

Return versioned `ResearchDocument` values with stable IDs, canonical URLs,
retrieval/publication timestamps, provenance, content and content hashes. Declare
pagination/truncation, unsupported capabilities and typed failure outcomes.
Connectors are read-only: the orchestration/research service records evidence and
charges; connectors never classify, cluster or mutate discovery state.

Register the implementation by its trusted dotted class path in
`DISCOVERY_CONNECTOR_CLASSES`. This is a deployment allowlist; agents cannot
import code or supply credentials. Do not disable public-fetch address checks
to reach a private source: that requires a separately scoped transport.

## Prototype verification and hardening

Prioritize one working start → search → Finder brief → Validator challenge →
Finder revision → report flow. Use focused offline provider fixtures and a real
PostgreSQL database for prototype smoke checks. Broad regression coverage,
concurrency failure injection and coverage gates follow in the final hardening
phase. Live paid acceptance runs require explicit authorization and a budget.

The earlier ingestion/clustering/investigation/ideation screens and schedules
remain available. New Discovery runs do not require legacy ingestion. Existing
enabled schedules are not automatically disabled by the migration; inspect
periodic tasks before providing credentials for unattended work.


### Repeatable offline prototype demonstration

With the development database migrated and Redis running:

```sh
.venv/bin/python scripts/smoke_discovery.py
```

This historical batch-path smoke uses real PostgreSQL, an extension-registry
fixture connector and `httpx.MockTransport` for OpenRouter traffic. It explicitly
selects `openrouter_batch`; it does not exercise the new Claude Code launcher. It demonstrates a four-turn
service proposal, saturation challenge, niche pivot and exact-revision agreement
through eight asynchronous model steps. The clearly labelled synthetic discovery
and ignored `.tmp/discovery-smoke-report.md` remain for inspection. These are
fixture conclusions, not market findings. No paid API calls are made.

The local host setup in this checkout uses PostgreSQL on 55432, Redis on 56379
and Django on 8088. Host `.env` addresses differ from Docker Compose service
addresses. Start host processes with:

```sh
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8088
.venv/bin/celery -A config worker --pool=solo --concurrency=1 -l info
.venv/bin/celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Run web, worker and beat in separate terminals. Discovery dispatch repair is
registered in Beat automatically; legacy ingestion schedules are not seeded.
Provider-reported costs and configured Tavily charge estimates are labelled
separately in reports; both consume the budget conservatively. An outstanding
remote batch is an unsettled possible charge, never assumed free.


### Host runtime requirements

Claude Code must be installed and executable by the Celery worker for every
provider. Ollama selection additionally requires the Ollama launcher. For
Anthropic, authenticate Claude Code on the same host account used by the worker,
or set its API key explicitly. An interactive login is a human setup step, not
an action performed by the discovery agent.

The repository's original Python Docker image does not provision Claude Code or
Ollama. Run the web/worker on a configured host for this prototype, or explicitly
build an image that supplies those runtimes and authentication. A missing binary
or unavailable selected provider is a visible configuration failure, not a reason
to route to a different backend.

Default provider/model settings:

```dotenv
DISCOVERY_RUNTIME=claude_code
DISCOVERY_FINDER_PROVIDER=openrouter
DISCOVERY_VALIDATOR_PROVIDER=openrouter
DISCOVERY_FINDER_MODEL=deepseek/deepseek-v4.1-flash
DISCOVERY_VALIDATOR_MODEL=deepseek/deepseek-v4.1-flash
DISCOVERY_JUDGE_ENABLED=True
DISCOVERY_JUDGE_PROVIDER=ollama
DISCOVERY_JUDGE_MODEL=kimi-k3:cloud
```


### Claude Code execution decisions and focused checks

Finder resumes its per-discovery Claude session. Validator starts a fresh session
for each review, receiving the latest proposal, objections and evidence. The
supervisor renews the task lease while Claude runs, enforces the deadline and
research-call limits, and stops the process family on cancellation. The mail
handoff is committed after wake usage is settled, so a following turn cannot
race ahead of accounting.

Claude receives only the discovery MCP research/submission tools: built-in file,
shell, browser and subagent tools are disabled. The installed Claude Code version
suppresses even explicitly configured MCP servers under `--safe-mode`, so this
implementation uses `--restricted`, empty settings sources, an explicit system
prompt, disabled slash commands and strict MCP configuration instead. This was
verified with the actual installed CLI, not inferred from its help text.

OpenRouter uses a loopback Anthropic-Messages gateway, exact model routing,
per-call reservations and an ephemeral child credential. The OpenRouter key
never enters Claude's environment or prompt. Anthropic uses an API key or the
worker account's existing Claude login. Ollama uses `ollama launch claude` with
isolated launcher preferences; local models must already be installed. Discovery
does not log in to accounts, pull models or change global Ollama preferences.

OpenRouter costs are provider-reported when available and explicitly estimated
otherwise. Native Anthropic uses Claude Code's spending cap and reported usage;
that cap is checked at client turn boundaries and is not a strict prepaid wallet.
Subscription valuations and Ollama cloud-plan charges are not verified invoices.
Unsettled operations remain visible rather than being assumed free.

Ollama Cloud usage is estimated from observed input, cached-input and output
tokens using published model rates, recorded with their source and verification
date. Claude Code's own dollar valuation is not an Ollama bill. Its reported
cached tokens are a separate category, not subtracted from uncached input twice.
Actual account credit deductions still require reconciliation with Ollama.

Ollama's dollar limit is checked between agent wakes. A wake can contain several
model requests and exceed the remaining estimate before usage is returned.
It is a soft application limit, not a provider-enforced billing cap.

Reconcile historical completed wakes without making model calls:

```sh
.venv/bin/python manage.py reconcile_discovery_costs --discovery DISCOVERY_ID
.venv/bin/python manage.py reconcile_discovery_costs --discovery DISCOVERY_ID --apply
```

The default is a dry run. Apply is idempotent and limited to terminal runs;
repeat `--discovery` to reconcile predecessors as well. Each run keeps its own
charges, so continuation totals must include their original runs. Unsettled
wakes remain explicitly incomplete. The command preserves Claude's original
valuation and records the published pricing source/date alongside the estimate.
New runtime snapshots use version 1.3 and cloud wake accounting uses version 1.1;
historical model/provider snapshots stay intact while their operation responses
gain the dated reconciliation basis. Refresh a persisted report with
`discovery.orchestrator.regenerate_report` after applying reconciliation.

Cloud models need a checked rate entry in `discovery/accounting.py`; unknown
tags are rejected during preflight until their published rates are registered.

Repeat the real-harness, provider-free demonstration with:

```sh
.venv/bin/python scripts/smoke_claude_discovery.py
```

It runs the installed Claude Code executable against simulated OpenRouter
Messages responses, real MCP tools and PostgreSQL, through four turns and twelve
model requests. It checks Finder session reuse, independent Validator sessions,
research, a saturation challenge, a niche pivot and final agreement. No paid
requests are made. Its report is `.tmp/claude-discovery-smoke-report.md` and is
clearly labelled synthetic. Anthropic/Ollama configuration and launcher contracts
were checked without invoking their paid inference or changing credentials.
