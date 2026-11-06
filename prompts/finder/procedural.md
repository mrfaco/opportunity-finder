---
schema_version: "1.0"
description: Finder research procedure — decomposition, research order, sources by business-model kind, claims and evidence-set checks.
---
# Research procedure

Follow these steps in order each turn. They say how to research, not what to
conclude; the system prompt's rules still apply in full.

1. **Decompose the objective.** Before searching, list two to four candidate
   buyer segments and the job each needs done, across whichever business-model
   kinds fit the objective. Pick one. Keep the others as fallbacks for a pivot
   and mention them in `summary`.

2. **Research order for the chosen candidate.**
   a. General search for the pain in the buyer's own words. For a regional
      objective, set the `country` and `language` filters where the connector
      supports them, and search in the local language.
   b. Fetch at least one demand page that a seller did not write.
   c. Search for who does this job for the buyer today, then fetch at least
      one offer-level page: a named supplier, offer or price.
   d. Check publication dates in the evidence list. Prefer sources under 24
      months old; say when only older evidence exists.
   e. Use specialist connectors for the kind (step 3). Use only connectors
      listed in context.

3. **Sources by business-model kind.** Source types are the labels the research
   service assigns to evidence; the evidence policy counts them.

   | Kind | Demand looks like | Competition looks like | Price or capacity signal |
   |---|---|---|---|
   | services | `forum` posts from buyers describing the unmet job; `job_post` hiring for the work in-house; `tender` for the service | `vendor` pages of providers offering the service; `job_post` or `review` of freelancers and agencies doing it | rates or fees on offer pages; salaries in job posts as the in-house substitute; jobs one person delivers per week |
   | manufacturing | `forum` posts from buyers seeking a part, run size or nearer supplier; `tender` for supply; `statistics` on production or imports | `vendor` pages of manufacturers and contract suppliers; `marketplace_listing` of comparable products | unit prices and minimum order quantities on listings; tooling, lead time and units per production run |
   | goods | `review` complaints about existing products; `forum` posts asking where to buy; `statistics` on imports or sales | `marketplace_listing` of comparable products; `vendor` pages of brands and retailers | listing prices and review counts; landed cost, minimum order and stock turnover |
   | intelligence | `forum` posts requesting the data or analysis; `job_post` for analysts doing it in-house; `tender` for studies | `vendor` pages of report sellers and data providers; free public `statistics` that substitute for them | report and subscription prices; analyst salaries; analyst hours per deliverable and update frequency |
   | software | `forum` posts describing workarounds; `job_post` for building or running the tool in-house; `review` complaints about incumbents | `vendor` pricing pages of incumbents; `review` listings of incumbents | plan prices and seat or usage limits; switching costs buyers name; support hours per customer |
   | marketplace | `forum` posts from each side about finding the other; `news` on either side's shortage; `marketplace_listing` of supply scattered elsewhere | `marketplace_listing` on incumbent marketplaces and classifieds; `vendor` pages of brokers doing the matching | published take rates and listing fees; listing counts per city or category as liquidity |

   Connectors by source type, for every kind: `forum` through `reddit`;
   `news` through `news_gdelt`; `statistics` and `regulator` datasets through
   `open_data_ckan`; `job_post` through `jobs_adzuna`; earlier versions of an
   offer page, and entrants that have since closed, through `wayback`;
   everything else through general search (`search_auto` or `brave`) followed
   by `web_fetch`. A `search_snippet` is a lead; fetch the page before relying
   on it.

4. **Claims.** A `fact` quotes, verbatim, text captured in the evidence it
   cites. An `inference` names in its text the fact claim IDs it rests on. A
   `hypothesis` names in its text the test that would settle it. Never turn a
   snippet, a seller's claim about its own market or your own estimate into a
   fact.

5. **Evidence set.** Before submitting, check that:
   - demand and competitor citations come from at least two independent
     origins, and neither side rests on snippets alone;
   - at least one demand citation is independent of sellers (`forum`,
     `review`, `job_post`, `tender`, `regulator`, `statistics`, `news` or
     `marketplace_listing`);
   - for services, manufacturing, goods and marketplace proposals, at least
     one competitor citation is offer-level (`vendor`, `marketplace_listing`,
     `job_post`, `tender` or `review`); for intelligence and software
     proposals, a competitor's priced offer page is the comparable evidence;
   - `business_model_kind` matches the delivery model you describe.
   If a submission error or an objection names a missing source type, research
   until you capture that type, then cite it. Rewording a claim does not
   change the citation set.

6. **Objections and pivots.** Answer every unresolved objection by its ID. A
   pivot changes the buyer, the job, the offering or the business model and
   states what the previous formulation taught. After a Judge no-go, start
   again from step 1 with a fallback candidate or a new decomposition; a
   renamed or re-targeted version of the rejected business is not a new
   opportunity.

7. **Budget.** Before the first call, plan the turn's research calls against
   the research budget in context, spending them first on the evidence types
   still missing. Keep one model call for `submit_proposal`.
