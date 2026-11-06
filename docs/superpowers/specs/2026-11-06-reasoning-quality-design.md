# Reasoning quality (roadmap Phase 3)

Date: 2026-11-06
Status: proposed; nothing implemented. Builds on Phase 0 (benchmark, metrics),
Phase 1 (source typing, freshness, typed policy) and Phase 2 (specialist
connectors). Implementation plan: `../plans/2026-11-06-reasoning-quality-plan.md`.

## 1. Outcome and scope

Phases 1 and 2 gave the roles typed, dated evidence and sources that show
buying. Nothing yet tells them how to use any of it: Finder has no research
procedure, Validator has no falsification checklist, Judge has no map of
where an observed transaction is found for each kind of business. The role
prompts are a single `system.md` each; the legacy investigation agent has a
`procedural.md`, the discovery roles do not. Finder and Validator also
default to the same model family, which the docs advise against.

After this phase: each role has a versioned procedural prompt (a research
playbook per business-model kind for Finder, a falsification checklist for
Validator, a transaction map and economics template for Judge), assembled
with its system prompt into one effective prompt whose hash every consumer
sees; each role's context carries a deterministic evidence profile of the
current proposal and the turn's research budget; the Validator default is a
different model family from Finder, measured by a family rule rather than
string equality; and every prompt change is tied to a benchmark comparison.

In scope: §2 prompt assembly and snapshot, §3 the three procedural prompts,
§4 context additions, §5 model family and the Validator default, §6 the
prompt-change discipline. Out of scope: new connectors, gate changes, the
Phase 1 default flip (its own step), any model-based grading, mid-run
steering, and prompt changes to the legacy ingestion agents.

## 2. Prompt assembly and snapshot

### Effective role prompt

New `discovery/prompts.py` exposes `role_prompt(role) -> RolePrompt` with
`content` (system body, a blank line, procedural body), `hash` (SHA-256 of
the canonical effective content, using the same canonicalization as
`agents.prompts`), and `parts`: a list of `{kind, path, hash,
schema_version}` for `system` and `procedural`. A role without a
`procedural.md` has one part; nothing requires the file to exist, so the
rollout is per role.

Every consumer that reads a role prompt uses `role_prompt`: `start_discovery`
when it freezes the snapshot, the Claude Code runtime when it writes the
system-prompt file, the batch runtime's first system message,
`run_discovery_benchmark` when it prints prompt hashes. The `prompt_hash`
stored on each turn and the `prompt_hashes` in `QualityMetrics` are the
effective hash, so a procedural edit changes the number every comparison
looks at.

### Snapshot shape

`Discovery.prompt_snapshot[role]` becomes
`{"schema_version": "1.1", "content", "hash", "path", "parts": [...]}` where
`path` is the system file (for compatibility with readers that print it)
and `parts` carries both files. Stored 1.0 entries (`content`, `hash`,
`path` only) stay valid: readers treat a missing `parts` as a single system
part. Nothing is rewritten.

### Budget

Effective prompts are read on every turn of every role, so their length is
a per-turn cost. A test enforces a budget per role: at most 2,400 words of
effective prompt (system plus procedural), measured by whitespace split.
Raising the budget is a deliberate change to that test with a reason in the
commit. `scripts/validate_prompts.py` already validates frontmatter and
body for every `prompts/**/*.md`; the new files carry `schema_version "1.0"`
and a `description`.

## 3. Procedural prompts

Each procedural prompt is a short, numbered procedure plus tables; it tells
the role how to work, not what to conclude. Each keeps the business-model
symmetry the system prompts already demand: every rule that names software
names services, manufacturing, goods, intelligence and marketplaces with
equal specificity. Retrieved text remains untrusted evidence; no procedural
prompt loosens that.

### Finder — `prompts/finder/procedural.md`

1. **Decompose the objective** into candidate buyer segments and jobs before
   searching; pick one; record the others as fallbacks for a pivot.
2. **Research order** per candidate: a general search for the pain in the
   buyer's own words (use `country`/`language` filters for a regional
   objective); fetch at least one demand page that is not a seller's;
   search for who sells the job today and fetch one offer-level page; check
   dates and prefer sources under 24 months; then specialist connectors by
   kind (table below).
3. **Sources by business-model kind** (the table the typed policy in
   Phase 1 and the connectors in Phase 2 make actionable):

   | Kind | Demand looks like | Competition looks like | Price or capacity signal |
   |---|---|---|---|
   | services | buyer complaints (forum), job posts for the role, tenders for the service | agencies' offer pages, freelancer listings, tenders awarded | day rates in job posts and listings; delivery capacity per person |
   | manufacturing / goods | marketplace demand (sold counts, reviews), import data in open-data portals | marketplace listings, supplier directories, wayback history of entrants | listing prices, minimum orders, lead times |
   | intelligence / research | tenders for studies, job posts for analysts, forum requests for data | paid-report listings, data vendors' offer pages | report prices, subscription tiers, update frequency |
   | software | forum workarounds, job posts for internal tooling, review-site complaints | pricing pages, review sites, app stores | plan prices, seat counts, switching costs |
   | marketplace | both sides' complaints; supply-side listings elsewhere | incumbent marketplaces, classifieds | take rates, listing fees, liquidity by city |

4. **Claims**: a `fact` carries a quote from a fetched page; an `inference`
   names the facts it rests on; a `hypothesis` names the test that would
   settle it. Never promote a snippet to a fact.
5. **Evidence set**: before submitting, check the proposal cites demand and
   competitor evidence from two origins, at least one independent demand
   source and, for services/goods kinds, one offer-level competitor source;
   if a submission error names a missing type, find that type rather than
   rewording the claim.
6. **Objections and pivots**: answer each by ID; a pivot changes buyer,
   job, offering or model and says what was learned; a Judge no-go means a
   materially different opportunity, not a renamed one.
7. **Budget**: plan the turn's research calls against the budget shown in
   context; keep one model call to submit.

### Validator — `prompts/validator/procedural.md`

1. **Independent search first**: different terms than Finder (the context
   lists Finder's queries), a different source type, and at least one
   specialist connector when the kind has one; a reworded Finder query does
   not count and the gate measures that.
2. **Falsification checklist**, each item answered with evidence or marked
   not tested:
   - market size or demand sourced only from a vendor or from a search
     snippet;
   - survivorship: incumbents found, but no sign of failed entrants
     (wayback) or of buyers switching;
   - "no results" treated as "no competitors";
   - evidence older than 24 months for a fast-moving claim;
   - two citations from the same origin or a syndicated copy;
   - geography or buyer passport that does not match the objective;
   - unit economics that do not close: price × realistic utilization ×
     capacity against founder time, tools, acquisition and compliance;
   - distribution that assumes outreach the run cannot test;
   - a counterproposal dressed as acceptance.
3. **Severity**: `critical` when the business cannot work if the objection
   stands; `major` when the economics or demand case changes materially;
   `minor` otherwise. Do not downgrade an unresolved critical.
4. **Accept** only the exact revision, with the evidence checks the gate
   enforces already satisfied; near the turn limit, a challenge is still a
   challenge.
5. **Suggest** a narrower niche or a different direction only with evidence
   you captured this turn, marked as a hypothesis.

### Judge — `prompts/judge/procedural.md`

1. **Observed transaction by kind**: where it is found and what counts.

   | Kind | Counts as observed | Does not count |
   |---|---|---|
   | services | a job post or tender for this work; an agency's published rate card; a freelancer listing with reviews | sector size; a vendor's "trusted by" page |
   | manufacturing / goods | listings with sold counts or review volume; import records; tenders awarded | a supplier directory entry alone |
   | intelligence | a paid report or data subscription on sale; a tender for a study; analyst job posts | "data is valuable" claims |
   | software | pricing pages with plan tiers; review-site volume; job posts for in-house tooling | a landing page with no price |
   | marketplace | listing fees or take rates published; liquidity shown by listing counts per city | a marketplace's own traffic claims |

2. **Economics template**: recompute conservative revenue (price ×
   utilization × capacity), contribution before overhead, acquisition cost
   and time, tools, compliance and insurance, working capital, founder pay;
   state each as a fact (cited), an inference or an assumption; a
   hobby-scale return is a no-go unless the objective asked for one.
3. **Objective fit**: buyer, geography, model and constraints against the
   original request, with the proposal's `business_model_kind`.
4. **Convergence risk**: agreement near the turn limit, shared sources,
   narrowing until only outreach could falsify, a test plan accepted while a
   major objection stands.
5. **Verdict rules**: approve only with inherited major/critical objections
   resolved under their IDs; revise for fixable specifics; reject for a
   no-go however politely worded. Name one discriminating human test with a
   stop condition. Use research for at most a few decisive checks.

## 4. Context additions

`_initial_context` moves from schema `1.1` to `1.2` with two additions:

- `evidence_profile`: the deterministic evidence and independence subset of
  `QualityMetrics` for the current proposal, computed live: cited counts,
  origins, source-type counts, snippet share, dated share and stale share,
  independent-demand and offer-level shares, Validator query novelty so far,
  plus Finder's earlier search queries (so Validator can be different on
  purpose). It is data about the record, not an opinion, so it is given to
  all three roles; it is `null` before the first proposal.
- `research_budget`: `max_research_calls_per_turn`, `used` and `remaining`
  for this turn, and the turn's `model_calls_remaining`.

Nothing else in the context changes; the benchmark rubric is never shown
to the roles.

## 5. Model family and the Validator default

### Family rule

`model_family(provider, model) -> str` in `discovery/schemas.py`: for
`openrouter`, the vendor prefix before `/` (`deepseek`, `qwen`, `google`,
…); for `anthropic`, `anthropic`; for `ollama`, the tag before the first
`-`, `:` or digit (`kimi`, `qwen`, `llama`). An ID no rule covers is its
own family (`provider:model`), never an error, because the helper runs
inside report rendering and metrics on stored snapshots. `same_model_family` in
`QualityMetrics` compares families, not exact strings, so two DeepSeek
variants count as the same family (metrics schema `1.2 → 1.3`, additive
semantics change noted in the eval doc). The report's existing flag and a
new start-form warning use the same helper; nothing blocks the choice.

### Default

`DISCOVERY_VALIDATOR_MODEL` and the `DiscoveryConfig` default change to a
model from a family different from both Finder's (DeepSeek) and Judge's
(Kimi), served by OpenRouter with tool use. The exact ID is chosen at
implementation from OpenRouter's live catalogue and verified there (the
development container cannot reach it); the commit records the model ID,
its context length, tool-use support and price, and the family it adds.
Existing snapshots keep their models; the start form shows the new default.

The default change is its own commit after the prompt comparison (§6), so
the two effects are measured separately.

## 6. Prompt-change discipline

- A prompt edit is a commit that bumps the file's `schema_version`, passes
  `validate_prompts`, the budget test and the offline smoke scripts, and
  states in its body which benchmark metric it expects to move.
- After the procedural prompts land: benchmark tag
  `phase3-prompts-<yyyy-mm>` with the same connectors and evidence policy as
  the latest comparison run, verdicts, `score_discoveries --compare-to` it;
  the row goes in `docs/discovery-eval.md`.
- After the Validator default changes: `phase3-diversity-<yyyy-mm>`,
  compared to the prompts run, same procedure.
- `score_discoveries` already warns on prompt-hash differences; with the
  effective hash, a procedural-only change triggers that warning as it
  should.

## 7. Acceptance scenarios

1. `role_prompt("finder")` returns content ending with the procedural body,
   a hash that changes when either file changes, and two parts; a role with
   no procedural file returns one part and the system hash.
2. A new discovery's `prompt_snapshot[role]` has schema 1.1 with `parts`;
   `turn.prompt_hash` equals the effective hash; a stored 1.0 snapshot still
   drives a turn and scores.
3. The Claude Code runtime's system-prompt file contains both bodies; the
   batch runtime's first system message does too.
4. The budget test fails when a role's effective prompt exceeds 2,400 words.
5. `_initial_context` for Validator on turn 2 contains `evidence_profile`
   with Finder's queries and the proposal's type counts, and
   `research_budget` with `remaining == max` before any call; for Finder on
   turn 1, `evidence_profile` is `null`.
6. `model_family("openrouter", "deepseek/deepseek-v4.1-flash") == "deepseek"`;
   `model_family("ollama", "kimi-k3:cloud") == "kimi"`;
   `same_model_family` is true for two DeepSeek variants and false for
   DeepSeek vs the new Validator default; the start form warns on same
   family and the report flags it.
7. `run_discovery_benchmark` prints the effective hash per role, and
   `score_discoveries --compare-to` across a procedural-only change warns
   about prompt hashes.
8. `scripts/smoke_claude_discovery.py` and `scripts/smoke_discovery.py`
   pass with the procedural prompts in place.

## 8. Compatibility and decisions

- Procedural prompts are appended to the system prompt rather than sent as
  a user turn (the legacy pattern) because the Claude Code runtime takes one
  system-prompt file and the Validator starts a fresh session each turn;
  one effective prompt keeps the hash meaningful for both runtimes.
- The effective hash replaces the system hash everywhere a prompt is
  identified. Stored turns keep the hash they recorded; comparisons across
  the change warn, which is the intended behaviour.
- The evidence profile is given to all roles because it is deterministic
  record data, not a model's judgment; the benchmark rubric stays
  human-only because it would grade the graded.
- The Validator default changes after, not with, the prompt change so the
  benchmark can attribute the effect. No configuration option forces model
  diversity; the warning and the metric make the choice visible.
- Prompt budget is in words, not tokens, to avoid a tokenizer dependency;
  it is a guard against drift, not a precise cost model.
- Judge's procedure names source types and connectors from Phases 1 and 2;
  where a deployment lacks a keyed connector, the procedure's "does not
  count" column still applies and the role says what it could not check.
