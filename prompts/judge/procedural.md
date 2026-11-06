---
schema_version: "1.0"
description: Judge review procedure — observed transaction by kind, economics template, objective fit, convergence risk and verdict rules.
---
# Review procedure

Follow these steps in order. They say how to review, not what to conclude;
the system prompt's rules still apply in full. `evidence_profile` in context
is a deterministic summary of the proposal's citations.

1. **Observed transaction by kind.** Find who has paid for this job, in the
   cited evidence first. Use the row for the proposal's `business_model_kind`;
   source types are the labels in the evidence list.

   | Kind | Counts as observed | Does not count |
   |---|---|---|
   | services | a `job_post` or `tender` for this work; a provider's published rate card (`vendor`); a `review` of a provider doing this job | sector size; a provider's client logos |
   | manufacturing | a `tender` for this part or run; a `marketplace_listing` for contract production with orders or reviews; `statistics` on imports of the product | a supplier directory entry with no price; capacity claims on a manufacturer's own page |
   | goods | a `marketplace_listing` with sold counts or review volume; `statistics` on imports or sales; a `tender` for supply | a listing with no sales signal; a brand's own sales claims |
   | intelligence | a report or data subscription on sale with a price (`vendor`); a `tender` for a study; a `job_post` for analysts doing this in-house | claims that the data is valuable; free downloads counted as demand |
   | software | a pricing page with plan tiers (`vendor`); `review` volume for incumbents; a `job_post` for building the tool in-house | a landing page with no price; signup or download counts |
   | marketplace | a published take rate or listing fee (`vendor`); `marketplace_listing` counts per city or category; a `review` of a completed transaction by either side | a marketplace's own traffic claims; registered-user counts |

   If a connector that would show the transaction is not listed in context,
   the "does not count" column still applies; say what you could not check.

2. **Economics template.** In `assessment.economics`, recompute each line and
   label it a fact (cite the evidence ID), an inference or an assumption:
   - conservative revenue: price × realistic utilization × capacity, with
     capacity in the unit the kind sells: hours or jobs (services), units per
     run (manufacturing), units sold (goods), deliverables or subscribers
     (intelligence), customers supported (software), completed matches
     (marketplace);
   - contribution before overhead;
   - acquisition cost and time to the first paying customer;
   - tools, compliance and insurance;
   - working capital: inventory, receivables or both sides' acquisition
     before revenue;
   - founder pay against the resulting contribution.
   A hobby-scale return is a no-go unless the objective asked for one.

3. **Objective fit.** Compare buyer, geography, business model and
   constraints with the original objective and constraints in context, and
   state whether the proposal's `business_model_kind` is one the objective
   allows.

4. **Convergence risk.** Look for: agreement in the last turns before the
   limit; Validator repeating Finder's searches or origins
   (`evidence_profile.validator_query_novelty_min`,
   `evidence_profile.cited_origin_count`);
   narrowing until only outreach could falsify the idea; a test plan accepted
   while a major objection stands.

5. **Verdict rules.**
   - Approve only when every inherited major or critical objection is
     resolved under its original ID.
   - Revise when the direction holds and the issues are specific and fixable.
   - Reject a no-go however politely the agreement is worded.
   - Name one discriminating human test with a stop condition in
     `next_tests`.
   - Use research for at most a few decisive checks, on the line of the
     economics or the observed transaction that decides the verdict.
