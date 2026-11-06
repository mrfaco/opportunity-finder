# Sources that show buying — implementation plan (roadmap Phase 2)

Date: 2026-11-05
Status: proposed; nothing implemented.
Specification: `../specs/2026-11-05-transaction-sources-design.md`.
Prerequisites: Phase 1 merged (source typing, `canonical_url` hook lands in
step 1 here), Phase 0 benchmark available for the comparison in step 8.

## Delivery strategy

Nine steps. Step 1 is shared groundwork; steps 2–7 are one connector or one
connector pair each, independent of one another after step 1, so two
engineers can take alternate steps on branches without conflicts (each
connector is its own module and test file; the registry and docs edits are
one-line appends). Step 8 wires defaults, metrics and docs; step 9 is the
paid comparison. Tier 2b connectors are listed as step 10 and are not part
of this phase's commitment.

No step changes prompts, gates or agent behaviour. A new connector is
visible to a run only when selected; the default list changes in step 8,
after every default connector has its fixtures and typed-failure tests.

AGENTS.md applies in full: tests in the same commit on real PostgreSQL where
persistence is involved, HTTP patched at the `httpx` boundary, absolute
imports, loud failures, no fallbacks between providers, coverage ratcheted at
the end.

Estimated effort: twelve working days for one engineer for steps 1–8; seven
to eight with two engineers on alternate connectors. Tier 2b: about five
more days.

## 1. Groundwork: HTTP helper, CSV fetch, typing hook, rules 1.1.0

Files: new `discovery/connectors/http.py`, `discovery/connectors/web.py`,
`discovery/research.py`, `discovery/source_type_rules.json`,
`discovery/source_types.py` (host patterns on any type are already
supported), `tests/test_connector_http.py`, `tests/test_web_fetch_csv.py`,
`tests/test_source_types.py` (extend), `docs/discovery.md`.

- `get_json`/`post_json` with the typed outcomes in spec §2; size cap; no
  redirects; identified `User-Agent`; reuse `_status_page`.
- `fetch_public_url`: accept CSV content types and `.csv` octet-stream under
  the 200 KB bound; `metadata["format"]="csv"`, `columns` from the header
  row; `web_fetch` version `1.2`; limitations text updated.
- `research.py`: classify by `metadata.get("canonical_url") or document.url`.
- Rules `1.1.0`: `adzuna.*`, `jooble.org` → `job_post`; rules test extended;
  `classify_evidence` dry run shows the version change.

Tests: each helper outcome from a fixture response; CSV fetch success,
truncation and non-CSV octet-stream still refused; a document with
`canonical_url` classifies by it while one without classifies by `url`; the
new rules classify their domains.

Done: every later connector can be written against one helper and typed
correctly.

## 2. `reddit`

Files: new `discovery/connectors/reddit.py`, `discovery/connectors/registry.py`,
`config/settings.py` (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`),
`.env.example`, `tests/test_connector_reddit.py`,
`tests/fixtures/connectors/reddit_search.json`, `reddit_thread.json`.

- Search, fetch, filters, cursor, record kinds and metadata per spec §3.
- Unauthenticated path and OAuth client-credentials path with token caching
  in the Django cache under a key scoped by client ID; the description
  names the active mode.

Tests: search parses the fixture into the expected records and kinds;
subreddit and recency map to the request; cursor round-trips; fetch orders
40 comments by score; 403 → `access_denied` with the OAuth hint; 429 with
`Retry-After` → `rate_limited`; OAuth mode sends the bearer token to
`oauth.reddit.com` and caches it; no network in any test.

Done: a role can read what buyers say, with the denial path visible.

## 3. `news_gdelt`

Files: new `discovery/connectors/gdelt.py`, registry, `tests/test_connector_gdelt.py`,
`tests/fixtures/connectors/gdelt_artlist.json`.

- Query composition with `sourcecountry`/`sourcelang` (ISO 639-1 → 639-3
  table for `es pt de fr en hi it nl pl`), `timespan` from `recency`,
  `maxrecords` from `limit`; snippets with publisher URL and `published_at`.

Tests: filter mapping on the outgoing request; fixture parsing; empty result
is success with no documents; GDELT's HTML error page (non-JSON) → typed
error; interval declared as 5 s.

Done: dated news with fetchable publisher URLs, by market.

## 4. `open_data_ckan`

Files: new `discovery/connectors/ckan.py`, registry, `config/settings.py`
(`DISCOVERY_CKAN_PORTALS` with the default list), `.env.example`,
`tests/test_connector_ckan.py`, `tests/fixtures/connectors/ckan_package_search.json`,
`ckan_package_show.json`.

- `portal` filter validated against the allowlist and required when more
  than one portal is configured; `format` filter; cursor `start`; dataset
  records with up to ten resources; fetch by dataset URL or `portal:id`.

Tests: allowlist enforcement (unknown portal refused, missing portal refused
with two configured, implied with one); fixture parsing including resources;
fetch by both target forms; portal 5xx → typed error naming the portal.

Done: government datasets are discoverable and their files fetchable.

## 5. `wayback`

Files: new `discovery/connectors/wayback.py`, registry,
`tests/test_connector_wayback.py`, `tests/fixtures/connectors/cdx.json`.

- CDX search with monthly collapse, year filters and match mode; capture
  records with `canonical_url`/`original_source_origin`; fetch through
  `fetch_public_url` with relabelling.

Tests: CDX parsing and filters; zero captures → success with the absence
note; fetch relabels origin and `canonical_url` so `classify` and
`evidence_gaps` treat the snapshot as the original site (persistence test on
PostgreSQL through `perform_research`).

Done: a competitor's history is evidence with the right origin.

## 6. `brave`

Files: new `discovery/connectors/brave.py`, registry, `discovery/schemas.py`
(`require_search` accepts `brave`), `config/settings.py`
(`BRAVE_SEARCH_API_KEY`, `DISCOVERY_BRAVE_SEARCH_COST_USD`), `.env.example`,
`tests/test_connector_brave.py`, `tests/fixtures/connectors/brave_web.json`.

- Request mapping for `country`/`language`/`recency`, `offset` cursor,
  subscription header from settings, cost upper bound as an estimate.

Tests: unconfigured → `configured=False` and preflight failure through
`start_discovery`; request mapping; fixture parsing with `published_at`;
cost recorded as estimate in the research ledger (PostgreSQL); 401 →
`access_denied`; `DiscoveryConfig` accepts `["brave", "web_fetch"]`.

Done: a region-aware search is available to deployments that want it.

## 7. `jobs_adzuna`

Files: new `discovery/connectors/adzuna.py`, registry, `config/settings.py`
(`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`), `.env.example`,
`tests/test_connector_adzuna.py`, `tests/fixtures/connectors/adzuna_search.json`.

- Required `country` from the market list, `location`, `recency` →
  `max_days_old`, page cursor; advertisement records with salary metadata.

Tests: market list enforcement with the Jooble pointer for `ar`; request
mapping; fixture parsing typed `job_post` through `classify`; 403 (bad key)
→ `access_denied`.

Done: paying employers are evidence, with salary bands.

## 8. Defaults, metrics 1.2, docs

Files: `config/settings.py`, `discovery/schemas.py` (`DiscoveryConfig`
connectors default; `QualityMetrics` 1.2), `.env.example`,
`discovery/quality.py`, `discovery/management/commands/score_discoveries.py`,
`discovery/reporting.py` (Evidence quality lines), `docs/discovery.md`
(connector table, filters, keys), `docs/discovery-eval.md` (comparison
procedure and 1.2 metrics note), `AGENTS.md` (one line: connectors use
documented APIs only; no scraping behind anti-bot walls), `README.md`
connector mention, `pyproject.toml` via `make coverage-ratchet`,
`tests/test_discovery_quality.py` and `tests/test_discovery_report_quality.py`
(extend), `tests/test_discovery_connectors_api.py` (new: `/api/v1/connectors/`
lists every connector with configuration status).

- Default list `search_auto,web_fetch,reddit,news_gdelt,open_data_ckan,wayback`;
  historical snapshots untouched.
- `transaction_signal_cited` and `specialist_connector_share`; aggregates
  and report lines; eval doc notes the metrics without a 1.1 counterpart.
- Ratchet coverage after the suite passes.

Tests: defaults resolve and preflight without keys; metrics on fixtures
citing an Adzuna record and citing only web pages; `--compare-to` across
1.1 and 1.2 warns; connectors endpoint shape.

Done: new discoveries reach the new sources by default and the benchmark can
measure whether the roles use them.

## 9. Benchmark comparison (human-authorized, paid)

Files: `docs/discovery-eval.md` (comparison row).

- `run_discovery_benchmark --tag phase2-sources-<yyyy-mm> --authorize-spend
  --max-total-usd 120` with the new default connectors and the **same
  evidence policy as the baseline** (`origins`), so the comparison isolates
  the effect of sources from the effect of the Phase 1 gate.
- Verdicts; `score_discoveries --compare-to <baseline run>`; record the
  deltas on `transaction_signal_cited`, `specialist_connector_share`,
  `cited_source_type_counts`, `demand_independent_source_share`, cost and
  verdicts; note which connectors the roles actually called
  (`cited_connector_counts` and the research ledger).
- Decide Tier 2b order from use: if job and tender types are cited, schedule
  `jobs_jooble`, `tenders_ted`, `tenders_sam`; if marketplace evidence is
  missing for goods proposals, `mercadolibre` first.

Done: the next connectors are chosen from observed use, not from a list.

## 10. Tier 2b (scheduled after step 9)

One commit per connector, same files pattern and test list as steps 2–7:
`jobs_jooble`, `tenders_ted`, `tenders_sam`, `mercadolibre`, each with its
settings key(s), fixture, request-mapping test, typed-failure tests and a
docs row. MercadoLibre additionally tests the unauthenticated denial path
and the client-credentials path.

## Primary implementation risks

- **Provider behaviour differs from documentation** (Reddit denials from
  cloud IPs, MercadoLibre auth, GDELT query quirks). Mitigation: fixtures
  come from one real response captured by hand and committed; the first live
  discovery after deploy is reviewed for each connector's ledger entries;
  every failure is typed and visible, so a broken connector costs one
  research slot, not a run.
- **Default list growth changes agent behaviour.** More connectors means
  more choice per research call within the same budget. Mitigation: the
  comparison in step 9 is run before any further default change; the
  research ledger shows whether the roles spread calls or ignore new
  sources, which informs Phase 3's playbooks.
- **Fixture drift.** Golden fixtures age as providers change fields.
  Mitigation: parsers read only the fields the spec names and treat missing
  optional fields as `None`; a schema change at the provider shows up as a
  typed parse error in the ledger, not an exception.
- **Portal allowlist as a settings list.** A typo silently removes a portal.
  Mitigation: `describe()` lists the configured portals; the connectors
  endpoint shows them; a test asserts the default list parses.
- **Key quotas on free tiers** (Brave 2,000/month, Adzuna 250/day). Mitigation:
  per-connector `minimum_interval_seconds`, the existing per-turn research
  limit, and the cost estimate for Brave; quota exhaustion is a 429 →
  `rate_limited` with the provider's reset time where given.

## Review assumptions before implementation

- The default connector list grows to six. Confirm, or keep `reddit` and
  `news_gdelt` only as the first defaults and add the other two after the
  comparison.
- `search_auto` stays DuckDuckGo → Bing; Brave is explicit-only. Confirm, or
  allow Brave as a configured third fallback (it would make a key-gated
  backend reachable by fallback, which the current design avoids).
- CKAN portal allowlist defaults to the four national portals that expose
  CKAN's API (AR, MX, DE, US). Confirm the list
  and whether regional portals (e.g. Buenos Aires city) belong in it.
- Tier 2b is gated on step 9. Confirm, or commit to all four now.
- Review sites stay out of scope because they would require scraping behind
  anti-bot walls. Confirm.
