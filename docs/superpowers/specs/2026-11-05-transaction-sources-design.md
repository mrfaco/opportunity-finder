# Sources that show buying (roadmap Phase 2)

Date: 2026-11-05
Status: proposed; nothing implemented. Builds on Phase 0 (metrics, benchmark)
and Phase 1 (source typing, freshness, typed policy, search filters).
Implementation plan: `../plans/2026-11-05-transaction-sources-plan.md`.

## 1. Outcome and scope

Judge is asked for the `observed_transaction`: who has actually paid for this
job, through this channel. Today no connector can answer that. The specialist
connectors are Hacker News, GitHub and Stack Overflow, which is a software
bias the product explicitly rejects, and the general search is English-biased.
After this phase the roles can reach, through the same read-only `Connector`
contract, sources that show demand as behaviour and competition as offers:
discussion by buyers, dated news, open government data, job advertisements,
public tenders, marketplace listings, a region-aware search API and the web
archive. Each result is typed by the Phase 1 rules so the typed policy and the
metrics can see the difference.

Connectors are grouped by what they need:

| Tier | Connector ID | Shows | Needs | Default |
|---|---|---|---|---|
| 1 | `reddit` | Recurring pain, workarounds, consumer and SMB demand | Nothing (optional OAuth app) | Yes |
| 1 | `news_gdelt` | Dated news with real publisher URLs, by country and language | Nothing | Yes |
| 1 | `open_data_ckan` | Government datasets: statistics, contracts, registries | Nothing (portal allowlist in settings) | Yes |
| 1 | `wayback` | Whether a competitor existed, changed or died | Nothing | Yes |
| 1 | `web_fetch` CSV | Open-data resources readable in place | Nothing | Already default |
| 2 | `brave` | Region-aware general search | `BRAVE_SEARCH_API_KEY` | Opt-in |
| 2 | `jobs_adzuna` | Companies paying for this work, with salary bands | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Opt-in |
| 2b | `jobs_jooble` | Same, covering Argentina and other Adzuna gaps | `JOOBLE_API_KEY` | Opt-in |
| 2b | `tenders_ted` | EU public procurement notices with values | `TED_API_KEY` | Opt-in |
| 2b | `tenders_sam` | US federal opportunities | `SAM_API_KEY` | Opt-in |
| 2b | `mercadolibre` | Listings and prices in AR, MX, BR and other LATAM sites | Optional app credentials | Opt-in |

Tier 1 and 2 are this phase's commitment. Tier 2b follows the same pattern
and is scheduled after the Tier 1 benchmark comparison shows which signal the
roles actually use; the spec covers it so the implementation is a repeat,
not a redesign.

Out of scope: review sites (Trustpilot, G2, app stores: anti-bot walls make a
connector dishonest; `web_fetch` already reads the pages that allow it), a
Product Hunt connector (the legacy adapter is a stub, not reusable),
statistics APIs beyond what CKAN portals expose (Eurostat, Comtrade: a later
phase), Google News RSS (rejected: its article links are opaque redirects
that `web_fetch` cannot follow, so the evidence URL would not be the
publisher), any prompt change beyond connector descriptions (Phase 3), and
connector health reporting (Phase 4).

## 2. Cross-cutting design

### One HTTP helper for API connectors

`discovery/connectors/http.py` exposes `get_json(url, *, params, headers,
timeout) -> JsonOutcome` and `post_json(...)`, wrapping `httpx` with the
behaviour every API connector repeats today: identified `User-Agent`,
fixed timeout, no redirects, response size cap (2 MB), typed outcomes for
transport errors, 401/403, 404, 429 with `Retry-After` (through the existing
`_status_page`), non-JSON bodies and non-2xx statuses. Connectors call fixed
API hosts; `fetch_public_url` with its public-IP checks remains the only way
to retrieve an arbitrary URL. The helper returns data or a `ResearchPage`
error; it never raises for a provider's behaviour.

### Typing hook for proxied content

`research.py` classifies by `metadata["canonical_url"]` when a connector sets
it, otherwise by the document URL. Only `wayback` sets it (the archived
original). `metadata["original_source_origin"]`, already honoured by
`evidence_gaps`, carries the same host so a snapshot and the live page count
as one origin. Source-type rules move to `1.1.0`: `adzuna.*` host pattern and
`jooble.org` under `job_post`; `ted.europa.eu` and `sam.gov` are already
tenders; `web.archive.org` is never matched directly.

### `web_fetch` reads CSV

`fetch_public_url` accepts `text/csv` and `application/csv` (and `.csv`
paths served as octet-stream) under the existing 200 KB bound, returning the
text with `metadata["format"] = "csv"` and a parsed `columns` list from the
header row. Open-data resources become readable evidence instead of an
unsupported-type error. Connector version `1.1 → 1.2`.

### Shared conventions

- Every connector: ≤ 250 lines, `describe()` with honest `access_limitations`,
  `configuration_requirements` and `configured`, `minimum_interval_seconds`
  from the provider's published limits, `cache_ttl_seconds` per source
  volatility, `kind` chosen by what the text is (`source_record` for a full
  API record, `search_snippet` for an excerpt that needs a fetch).
- Results carry `published_at` whenever the provider gives one, `author`
  where meaningful, and the raw provider identifiers in `metadata`.
- Pagination via the existing `cursor` where the provider offers it, with
  `pagination=True` in `validate_search`.
- Filters are declared in `supported_filters`, validated before any request
  (`validate_market_filters` for `country`/`language`/`recency`), and listed
  in the connector's name or limitations so the roles see them.
- Keys live in settings and `.env.example`; a missing key is
  `configured=False` with a `configuration_error`, which `start_discovery`
  already turns into a visible preflight failure. No connector reads a key
  from agent input.
- Tests: one golden provider fixture per connector under
  `tests/fixtures/connectors/`, HTTP patched at the `httpx` boundary as the
  existing connector tests do, and one assertion per typed failure outcome.

## 3. Tier 1 connectors

### `reddit`

- Search: `https://www.reddit.com/search.json` (`q`, `limit` ≤ 25, `sort`,
  `t`, `after`) or `/r/<subreddit>/search.json` with `restrict_sr=1`.
  Filters: `subreddit` (`[A-Za-z0-9_]{2,21}`), `recency` → `t`, `sort`
  (`relevance`, `top`, `new`, `comments`). Cursor: `after`.
- Each post becomes a `source_record` when its `selftext` is complete (Reddit
  returns the full body) and a `search_snippet` for link posts without body;
  `metadata` carries `subreddit`, `score`, `num_comments`, `permalink`,
  `is_self`, `link_url`.
- Fetch: a post permalink or ID → `<permalink>.json` with the post and up to
  40 top-level comments ordered by score, as one `source_record`; comments are
  prefixed with author and score like the Hacker News fetch.
- Auth: unauthenticated by default with a descriptive `User-Agent`. When
  `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are set, the connector obtains a
  client-credentials token and uses `oauth.reddit.com`; the token is cached
  for its lifetime minus a minute. Both modes have the same
  `configured=True`; the description names which is active.
- Limits: `minimum_interval_seconds` 6 unauthenticated, 1 with OAuth; 429
  honours `Retry-After`; 403 from a blocked IP is `access_denied` with the
  reason "Reddit denied this client; configure OAuth credentials".

### `news_gdelt`

- Search: GDELT DOC 2.0 `https://api.gdeltproject.org/api/v2/doc/doc` with
  `mode=artlist`, `format=json`, `maxrecords` ≤ 25, `query` built from the
  user query plus `sourcecountry:<CC>` and `sourcelang:<iso3>` when
  `country`/`language` are given (ISO 639-1 → 639-3 table for the benchmark
  languages, extendable), and `timespan` from `recency` (`1d`, `1w`, `1m`,
  `12m`, since the DOC API has no year unit; default `3m`).
- Each article is a `search_snippet` with the publisher URL as `url`,
  `published_at` from `seendate`, `metadata` with `domain`, `sourcecountry`,
  `language`, `socialimage` omitted. The role fetches the article with
  `web_fetch`, so the evidence record is the publisher page and classifies
  by the publisher domain.
- No key. `minimum_interval_seconds` 5 (GDELT asks for ≤ 1 request per 5 s).
  GDELT's 15-minute update lag and English-centred coverage of small outlets
  are stated in `access_limitations`.

### `open_data_ckan`

- One connector for every CKAN portal in `DISCOVERY_CKAN_PORTALS`, a settings
  allowlist of base URLs defaulting to the benchmark regions' national
  portals whose API is CKAN's `/api/3/action`: `datos.gob.ar`,
  `datos.gob.mx/busca`, `www.govdata.de/ckan`, `catalog.data.gov`.
  (`dados.gov.br` and `datos.gob.es` run their own APIs; `data.gouv.fr` is
  `udata`; `data.gov.in` is custom. A later connector can add them.)
- Search: `/api/3/action/package_search` with `q`, `rows` ≤ 20, `start`
  (cursor), optional `fq=res_format:<FORMAT>`. Filter `portal` selects one
  host from the allowlist and is required when more than one is configured
  (no silent fan-out across governments); filter `format` is `CSV`, `JSON`,
  `XLSX` or `PDF`.
- Each dataset is a `source_record`: title, notes (≤ 4,000 characters),
  organization, tags, `metadata_modified` as `published_at`, and up to ten
  resources as `(name, format, url, size)` in `metadata["resources"]` so the
  role can `web_fetch` a CSV or PDF directly.
- Fetch: a dataset URL or `portal:id` → `/api/3/action/package_show`, same
  shape with all resources.
- No key; `minimum_interval_seconds` 1; portal outages are typed errors
  naming the portal.

### `wayback`

- Search: CDX `https://web.archive.org/cdx/search/cdx` with `url=<target>`,
  `output=json`, `filter=statuscode:200`, `collapse=timestamp:6` (one per
  month), `limit` ≤ 20, optional `from`/`to` years. The "query" is the URL
  or host to inspect; filters `from_year`, `to_year`, `match` (`exact`,
  `prefix`). Each capture is a `source_record` with the snapshot URL,
  `published_at` = capture timestamp, `metadata` with `original_url`,
  `canonical_url`, `original_source_origin`, `mimetype`, `statuscode`.
  A zero-capture result is a success with no documents and a note that
  absence from the archive is not absence from the web.
- Fetch: a snapshot URL (`https://web.archive.org/web/<ts>id_/<url>`) through
  `fetch_public_url`, then the document is re-labelled with `canonical_url`
  and `original_source_origin` set to the archived original so typing and
  origin counting treat it as that page on that date.
- No key; `minimum_interval_seconds` 2; the archive's own rate limiting is
  passed through as `rate_limited`.

## 4. Tier 2 connectors

### `brave`

- Search: `https://api.search.brave.com/res/v1/web/search` with `q`,
  `count` ≤ 20, `offset` (cursor), `country` (upper-cased), `search_lang`,
  `freshness` from `recency` (`pd`, `pw`, `pm`, `py`). Header
  `X-Subscription-Token` from `BRAVE_SEARCH_API_KEY`. Results are
  `search_snippet`s with `published_at` from `age`/`page_age` when present.
- Cost: `DISCOVERY_BRAVE_SEARCH_COST_USD` (default `0`, the free tier) is the
  `search_cost_upper_bound_usd` and is labelled an estimate like Tavily's.
- Accepted by `DiscoveryConfig.require_search` as a general web search.
  `search_auto` is unchanged (DuckDuckGo → Bing); Brave is an explicit
  selection, so no key-gated backend is ever reached by fallback.

### `jobs_adzuna`

- Search: `https://api.adzuna.com/v1/api/jobs/<country>/search/<page>` with
  `app_id`, `app_key`, `what`, `where` (filter `location`), `results_per_page`
  ≤ 20, `max_days_old` from `recency`, `sort_by=date`. Filter `country` is
  required and must be one of Adzuna's markets (`at au be br ca ch de es fr
  gb in it mx nl nz pl sg us za`); Argentina is not covered, which the
  description states and `jobs_jooble` fills.
- Each advertisement is a `source_record`: title, company, location,
  description (≤ 3,000 characters), `salary_min`/`salary_max`/`currency` and
  `contract_time` in `metadata`, `created` as `published_at`, the Adzuna
  redirect URL as `url` (typed `job_post` by the `adzuna.*` rule).
- Cursor: page number. `minimum_interval_seconds` 1.

## 5. Tier 2b connectors (same pattern, scheduled later)

- `jobs_jooble`: `POST https://jooble.org/api/<key>` with `keywords`,
  `location`, `page`; covers AR, MX, BR, DE, FR, ES, US, IN; records shaped
  like Adzuna's; typed `job_post` via `jooble.org`.
- `tenders_ted`: TED Search API v3 `POST /v3/notices/search` with an expert
  query built from the user query, `country` → buyer country, `recency` →
  publication date range; notice title, buyer, CPV, estimated value and
  currency in `metadata`; `source_record`; typed `tender`.
- `tenders_sam`: `https://api.sam.gov/opportunities/v2/search` with `api_key`,
  `title`/`q`, `postedFrom`/`postedTo` from `recency`, NAICS optional;
  `source_record`; typed `tender`.
- `mercadolibre`: `https://api.mercadolibre.com/sites/<SITE>/search` with
  `q`, `limit` ≤ 20, `offset`; `country` → site (`ar`→`MLA`, `mx`→`MLM`,
  `br`→`MLB`, `cl`→`MLC`, `co`→`MCO`, `uy`→`MLU`, `pe`→`MPE`); each item a
  `source_record` with price, currency, sold quantity, seller type and
  condition in `metadata`; typed `marketplace_listing`. When the endpoint
  refuses unauthenticated calls, the connector reports `access_denied`
  naming the optional `MERCADOLIBRE_APP_ID`/`MERCADOLIBRE_APP_SECRET`
  client-credentials path, which it uses when configured.

## 6. Defaults, configuration and metrics

- `DISCOVERY_CONNECTORS` default becomes
  `search_auto,web_fetch,reddit,news_gdelt,open_data_ckan,wayback`
  (settings, `DiscoveryConfig`, `.env.example`). Only keyless connectors
  are defaults because `start_discovery` preflights every selected
  connector's configuration; an opt-in connector with a missing key fails
  the start visibly, as today. Existing snapshots keep their lists.
- New settings: `BRAVE_SEARCH_API_KEY`, `DISCOVERY_BRAVE_SEARCH_COST_USD`,
  `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REDDIT_CLIENT_ID`,
  `REDDIT_CLIENT_SECRET`, `DISCOVERY_CKAN_PORTALS`; Tier 2b adds
  `JOOBLE_API_KEY`, `TED_API_KEY`, `SAM_API_KEY`, `MERCADOLIBRE_APP_ID`,
  `MERCADOLIBRE_APP_SECRET`. All documented in `.env.example` with the
  provider's free-tier limits in a comment.
- `/api/v1/connectors/` and the start form list the new connectors through
  the existing `describe_connectors()`.
- `QualityMetrics` `1.1 → 1.2` adds `transaction_signal_cited: bool | None`
  (any cited evidence typed `job_post`, `tender` or `marketplace_listing`,
  `None` without a final proposal) and
  `specialist_connector_share` (cited rows from connectors other than
  `web_fetch`, `web_search`, `search_auto`, `duckduckgo`, `bing`, `brave` ÷
  cited rows). These are the roadmap's exit-gate numbers.
- Connector descriptions are the only text the roles see about a source;
  each `access_limitations` says what the source proves and what it does
  not, in one sentence, so no prompt change is needed in this phase.

## 7. Acceptance scenarios

1. With the default connector list, a new discovery starts without any key
   configured; selecting `brave` without `BRAVE_SEARCH_API_KEY` fails
   preflight with the key named.
2. `reddit` search for a query returns posts with full bodies as
   `source_record`s typed `forum`; a 403 yields `access_denied` with the
   OAuth hint; with OAuth settings the request goes to `oauth.reddit.com`
   with a bearer token; a 429 with `Retry-After: 30` yields `rate_limited`
   with `retry_after_seconds` 30.
3. `news_gdelt` with `{"country": "ar", "language": "es", "recency":
   "month"}` sends `sourcecountry:AR sourcelang:spa` and `timespan=1m`;
   results carry publisher URLs and `published_at`; fetching one with
   `web_fetch` yields evidence typed by the publisher domain.
4. `open_data_ckan` with two portals configured refuses a search without
   `portal`; with `portal=datos.gob.ar` it returns datasets with resource
   URLs; `web_fetch` of a CSV resource returns text with `format: csv` and
   `columns`; a 200 KB+ CSV is truncated and flagged as today.
5. `wayback` search for a competitor host returns monthly captures with
   `canonical_url` set; fetching one yields evidence whose `origin` and
   `source_type` are the archived site's, and `evidence_gaps` counts it as
   the same origin as the live page.
6. `brave` with `country=ar`, `language=es`, `recency=week` sends `country=AR`,
   `search_lang=es`, `freshness=pw`; cost is recorded as the configured
   estimate; `DiscoveryConfig` accepts `["brave", "web_fetch"]`.
7. `jobs_adzuna` with `country=mx` and `location=Monterrey` returns
   advertisements typed `job_post` with salary metadata; `country=ar` is
   refused with a message pointing at `jobs_jooble`.
8. `compute_quality` on a proposal citing an Adzuna record reports
   `transaction_signal_cited=True`; on one citing only web pages, `False`;
   `score_discoveries --compare-to` a 1.1 run warns about the schema change.
9. Every new connector's typed failure outcomes (transport error, 401/403,
   404, 429, non-JSON body, oversized body) are asserted from fixtures, and
   none raises for a provider's behaviour.

## 8. Risks and decisions

- **Reddit may block cloud IPs.** Unauthenticated JSON works from most
  addresses but data-centre ranges often get 403. Decision: ship
  unauthenticated with the OAuth path available; a denial is a visible
  `access_denied`, never a silent empty result. If the baseline shows
  frequent denials, document OAuth as the recommended setup.
- **GDELT is a research service** with occasional outages and a 5-second
  interval. Decision: accept; it is the only keyless dated-news source with
  real URLs. `search_auto`'s connectors still find news when GDELT is down.
- **Open data is datasets, not answers.** A CKAN result tells the role where
  the numbers are; reading them needs a fetch and often a large file.
  Decision: CSV in `web_fetch` under the 200 KB bound covers summary tables;
  larger files are a stated limitation, not a new transport.
- **MercadoLibre's search endpoint may require OAuth.** Decision: Tier 2b,
  with the credentials path specified and the denial visible.
- **Provider terms.** Every connector uses documented public APIs or
  endpoints the provider offers for programmatic use, identifies itself,
  respects published intervals and caches. No HTML scraping of job boards,
  review sites or marketplaces is added; where the public API does not
  exist, the source is out of scope rather than scraped.
- **Keys in agent context.** Keys are read from settings inside the
  connector; tool inputs, evidence, logs and reports never carry them, and
  `describe()` reports only whether a key is configured.
- **Connector count.** Four keyless defaults plus two opt-ins is the
  commitment; Tier 2b is specified but gated on the Tier 1 comparison so
  effort follows evidence of use.
