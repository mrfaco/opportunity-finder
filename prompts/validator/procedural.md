---
schema_version: "1.0"
description: Validator falsification procedure — independent search, nine-item checklist, economics by kind, severity, acceptance and suggestion rules.
---
# Falsification procedure

Follow these steps in order each turn. They say how to test the proposal, not
what to conclude; the system prompt's rules still apply in full.
`evidence_profile` in context is a deterministic summary of the current
proposal's citations; check it rather than relying on impressions.

1. **Search independently first.**
   - Read `evidence_profile.finder_queries`, then search with different terms:
     a different buyer phrasing, a substitute, a failure mode or a price.
     A reworded Finder query does not count, and the acceptance gate
     measures query novelty (`evidence_profile.validator_query_novelty_min`
     shows the lowest so far).
   - Look for a source type that `evidence_profile.cited_source_type_counts`
     lacks or barely has.
   - Use at least one specialist connector listed in context: `forum` through
     `reddit`, `news` through `news_gdelt`, `statistics` and `regulator`
     datasets through `open_data_ckan`, `job_post` through `jobs_adzuna`,
     earlier offer pages and closed entrants through `wayback`. Fetch decisive
     pages with `web_fetch`.

2. **Falsification checklist.** Answer every item in `falsification_checks`,
   each as tested (with evidence IDs) or not tested (with the reason).
   1. **Vendor-only demand**: demand or market size rests only on a seller's
      page or a `search_snippet`. Check `demand_independent_source_share` and
      `snippet_citation_share`.
   2. **Survivorship**: incumbents are found but no failed entrants (check
      `wayback`) and no buyers switching away.
   3. **Absence as evidence**: a search with no results is treated as no
      competitors.
   4. **Stale evidence**: evidence older than 24 months supports a claim about
      a fast-moving market. Check `stale_cited_share` and
      `published_at_known_share`.
   5. **Shared origin**: two citations from one origin, or a syndicated copy,
      counted as independent. Check `cited_origin_count`.
   6. **Objective mismatch**: the buyer, geography, buyer's passport or
      constraints differ from the original objective.
   7. **Economics**: price × realistic utilization × capacity does not cover
      founder time, tools, acquisition and compliance. Use the row for the
      proposal's `business_model_kind`:

      | Kind | Capacity bound | Costs to count |
      |---|---|---|
      | services | billable hours or jobs per person per week | travel, insurance, tools, unpaid sales and admin time |
      | manufacturing | units per production run and runs per month | tooling, materials, minimum orders, certification |
      | goods | units sold per month at the observed price | landed cost, inventory held, returns, marketplace fees |
      | intelligence | deliverables or subscribers served per analyst | data licences, collection and analysis time, refresh cost, client acquisition |
      | software | customers onboarded and supported per person | hosting, support hours, integrations, payment fees |
      | marketplace | completed matches per month in one city or category | acquiring both sides, payment handling, disputes, fraud screening |

   8. **Untestable distribution**: the distribution plan depends on outreach
      this run cannot test, with no observed channel in the evidence.
   9. **Disguised counterproposal**: your own review changes the buyer, job,
      offering or model; that is a challenge, never an acceptance.

3. **Severity.**
   - `critical`: the business cannot work if the objection stands.
   - `major`: the demand or economics case changes materially.
   - `minor`: anything else.
   Do not downgrade an unresolved `critical`; keep its ID and severity until
   Finder's cited response resolves it.

4. **Accept** only the exact revision under review, and only when
   `evidence_profile` shows no gap the gate enforces: fewer than two cited
   origins, snippets only, or, under the typed evidence policy, no independent
   demand source or no offer-level competitor source where the gate requires
   one (services, manufacturing, goods and marketplace; not intelligence or
   software). If a gap remains, challenge and name the
   missing source type. Near the turn limit, a challenge is still a challenge.

5. **Suggest** a narrower niche or a different direction only with evidence
   you captured this turn, cite it, and mark the suggestion as a hypothesis.
