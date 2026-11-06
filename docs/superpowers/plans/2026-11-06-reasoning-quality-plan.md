# Reasoning quality — implementation plan (roadmap Phase 3)

Date: 2026-11-06
Status: proposed; nothing implemented.
Specification: `../specs/2026-11-06-reasoning-quality-design.md`.
Prerequisites: Phases 0–2 merged. The latest benchmark comparison run (Phase
2 step 9, or the Phase 0 baseline if that has not happened) is the reference
for step 8.

## Delivery strategy

Nine steps. Step 1 is the prompt-assembly groundwork every other step relies
on. Steps 2–4 are the three procedural prompts, each its own commit so a
reviewer reads one role at a time; they are independent of each other after
step 1. Step 5 adds context; step 6 adds the family rule and the warning but
does not change the default; step 7 is docs and the ratchet; step 8 is the
paid prompt comparison; step 9 changes the Validator default and runs its
own comparison, so each effect is measured alone.

AGENTS.md applies: prompts live in git with bumped `schema_version`; every
behaviour change ships with its test on real PostgreSQL; absolute imports;
loud failures; coverage ratcheted at the end. Writing the procedural prompts
is the bulk of the work and the part that most needs a second reader: each
prompt commit should be reviewed by someone who did not write it, against
the business-model symmetry rule in spec §3.

Estimated effort: eight working days for one engineer for steps 1–7, of
which about four are prompt writing and review; steps 8–9 are benchmark
wall-clock time plus two afternoons of verdicts.

## 1. Effective role prompt and snapshot 1.1

Files: new `discovery/prompts.py`, `discovery/orchestrator.py`
(`start_discovery` snapshot), `discovery/claude_runtime.py` (system-prompt
file), `discovery/loop.py` (batch first message, `prompt_hash`),
`discovery/management/commands/run_discovery_benchmark.py` (printed hashes),
`discovery/quality.py` (reads `hash` as before; no change expected, verify),
`tests/test_role_prompts.py`, `tests/discovery_fixtures.py` (snapshot
builder gains `parts`).

- `RolePrompt` and `role_prompt(role)` per spec §2; canonicalization and
  hashing through `agents.prompts.canonicalize`.
- Snapshot entries at schema 1.1 with `parts`; readers accept 1.0.
- Budget test: 2,400 words per role, effective content.

Tests: hash changes on either file; one part without a procedural file;
snapshot shape on a new discovery; a 1.0 snapshot still drives the batch
path and the Claude Code prompt file; benchmark command prints the
effective hash; budget test passes for the current prompts and fails for a
synthetic oversized one.

Done: there is one effective prompt per role and one hash for it.

## 2. Finder procedural prompt

Files: new `prompts/finder/procedural.md` (1.0), `prompts/finder/system.md`
(1.3 only if a sentence moves to the procedure; otherwise unchanged),
`tests/test_role_prompts.py` (extend).

- Write spec §3 Finder as a numbered procedure with the sources-by-kind
  table. Every row names demand, competition and price/capacity for its
  kind with equal specificity; the connectors named exist in this repo
  (`reddit`, `news_gdelt`, `open_data_ckan`, `wayback`, `jobs_adzuna`,
  `brave`, `web_fetch`, `search_auto`).
- No instruction may conflict with the gates: cite only returned IDs,
  quotes verbatim, no outreach, no convergence claims.

Tests: file validates (`validate_prompts`); budget holds; a symmetry check
asserts the table has one row per `BusinessModelKind` value except `other`;
`role_prompt("finder").parts` has two entries.

Done: Finder has a research procedure a reviewer approved for symmetry.

## 3. Validator procedural prompt

Files: new `prompts/validator/procedural.md` (1.0),
`tests/test_role_prompts.py` (extend).

- Spec §3 Validator: independence first, the falsification checklist, the
  severity rule, acceptance and suggestion rules. The checklist names the
  deterministic signals the context now carries (Finder's queries, type
  counts, stale share) so the role checks against data, not impressions.

Tests: validates; budget; the checklist mentions each of the nine items by
a stable keyword (a test guards against silent deletion of an item);
`parts` has two entries.

Done: Validator has a checklist a reviewer approved.

## 4. Judge procedural prompt

Files: new `prompts/judge/procedural.md` (1.0), `tests/test_role_prompts.py`
(extend).

- Spec §3 Judge: the observed-transaction table by kind, the economics
  template, fit, convergence risk, verdict rules and the single human test.

Tests: validates; budget; the table has one row per kind except `other`;
`parts` has two entries.

Done: Judge knows where a transaction is found for each kind of business.

## 5. Context: evidence profile and research budget

Files: `discovery/loop.py` (`_initial_context` 1.2), `discovery/quality.py`
(a public `evidence_profile(discovery, proposal)` built from the existing
private metric helpers), `discovery/research.py` (budget numbers already
computed by `research_calls_used` and the model-call count),
`tests/test_discovery_context.py` (new), `docs/discovery.md`.

- `evidence_profile` and `research_budget` per spec §4; `null` profile
  before the first proposal; Finder's prior queries listed for Validator and
  Judge.
- The benchmark rubric is asserted absent from every role's context.

Tests: scenario 5 in the spec on PostgreSQL fixtures; schema version 1.2;
rubric absence; a 1.0/1.1 stored context (in `DiscoveryTask.context`) is
not rewritten or re-validated.

Done: the roles see the deterministic shape of the evidence and their
remaining budget.

## 6. Model family rule and warning

Files: `discovery/schemas.py` (`model_family`), `discovery/quality.py`
(`same_model_family` by family; `QualityMetrics` 1.3), `discovery/reporting.py`
(flag text names the families), `discovery/admin.py` (start-form warning
message on same family), `docs/discovery-eval.md` (1.3 note),
`tests/test_model_family.py`, `tests/test_discovery_quality.py` (extend).

- The helper and its three provider rules; metric and report use it; the
  form warns and does not block.

Tests: scenario 6 cases; the report flag appears for two DeepSeek variants
and not for different families; `--compare-to` across 1.2 and 1.3 warns.

Done: model diversity is measured by family and visible before a run starts.

## 7. Docs and ratchet

Files: `docs/discovery.md` (procedural prompts, effective hash, context 1.2,
family rule), `docs/discovery-eval.md` (prompt-change discipline, the two
comparison tags, 1.3 metrics note), `prompts/README.md` (discovery
procedural files and the effective-hash rule), `AGENTS.md` (one line: a
prompt edit bumps its version and names the metric it expects to move),
`CLAUDE.md` (pointer), `pyproject.toml` via `make coverage-ratchet`.

Done: the discipline is written where the next editor will read it.

## 8. Prompt comparison (human-authorized, paid)

Files: `docs/discovery-eval.md` (row).

- `run_discovery_benchmark --tag phase3-prompts-<yyyy-mm> --authorize-spend
  --max-total-usd 120` with the same connectors and evidence policy as the
  reference run; verdicts; `score_discoveries --compare-to <reference>`.
- Record deltas on grounding (`fact_claims_with_quote_rate`), type
  diversity, `demand_independent_source_share`,
  `competitor_offer_source_share`, `transaction_signal_cited`,
  `validator_query_novelty_min`, turns to convergence, cost, verdict split,
  and Judge–human agreement where verdict counts allow.
- If a metric regressed, the follow-up is a prompt edit with its own row,
  not a revert of the whole phase.

Done: the effect of the procedures is a number in the eval doc.

## 9. Validator default change and comparison (human-authorized, paid)

Files: `config/settings.py`, `.env.example`, `discovery/schemas.py`
(`DiscoveryConfig` default), `docs/discovery.md`,
`tests/test_discovery_config_defaults.py` (new: the default Validator
family differs from Finder's and Judge's), `docs/discovery-eval.md` (row).

- Choose the model from OpenRouter's live catalogue: a different family
  from DeepSeek and Kimi, tool use supported, context length and price
  recorded in the commit body. Verify the ID against the catalogue from a
  machine that can reach it; the development container cannot.
- `run_discovery_benchmark --tag phase3-diversity-<yyyy-mm> …` compared to
  the step 8 run; record the row; the Finder default is unchanged.

Done: the deployment default has model diversity and the eval doc shows
what it changed.

## Primary implementation risks

- **Prompts re-introduce a software bias.** The symmetry rule and the
  per-kind tables are the guard; the tests check structure, a human reviewer
  checks substance. Mitigation: the review step named in the delivery
  strategy, and the benchmark's `business_model_kind` distribution before
  and after.
- **Longer prompts cost tokens every turn.** The 2,400-word budget bounds it;
  the comparison records cost per approved proposal.
- **The Claude Code runtime may ignore a changed system prompt on
  `--resume`** for Finder's persistent session. Mitigation: step 1's test
  writes the prompt file on every turn as today; if a live check shows the
  resumed session keeps the first prompt, the follow-up is to key the
  session ID on the effective hash so a prompt change starts a new session,
  documented as such.
- **Model availability.** The chosen Validator model may be unavailable or
  change price. Mitigation: the ID is a settings default, the preflight
  already fails visibly when a provider refuses it, and the commit records
  the verification.
- **Confounded comparison.** Prompts and model change together would hide
  which helped; steps 8 and 9 are sequential by design.

## Review assumptions before implementation

- The procedural prompt is appended to the system prompt (one effective
  prompt), not sent as a first user message as the legacy agents do.
  Confirm.
- `evidence_profile` is given to all three roles. Confirm, or restrict it
  to Validator and Judge.
- The prompt budget is 2,400 words per role. Confirm the number.
- The Validator default moves to a third family (not DeepSeek, not Kimi)
  rather than to Anthropic `sonnet`, which would require the worker host to
  have Claude Code authenticated for Anthropic. Confirm.
- The default change waits for the prompt comparison. Confirm, or ship both
  and run one comparison.
