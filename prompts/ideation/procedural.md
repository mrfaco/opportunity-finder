---
schema_version: "1.0"
description: Procedural prompt — bet axis menu, concept structure, output rules for the ideation agent
---

# The bet axis menu

Each concept must declare a `bet_axis` drawn from this menu. The three
concepts in one ideation must use **three distinct axes** — that is the
forcing function that prevents convergence on a single framing.

- `aggressive_scope` — build the full vision: end-to-end platform / control
  plane / multi-feature suite that fully replaces the workarounds today.
- `minimal_scope` — single-feature wedge: ship the one thing that's most
  obviously missing, become indispensable for that, expand later.
- `different_buyer` — same pain, different decision-maker. The brief
  identifies one buyer; reframe around an adjacent one (e.g. the team
  lead instead of the engineer, the agency owner instead of the
  freelancer, the IT admin instead of the end user).
- `open_source_vs_saas` — distribution bet. If the obvious play is SaaS,
  consider open-source with a paid hosted tier; or vice versa.
- `self_serve_vs_sales_led` — go-to-market motion bet. If the obvious
  play is sales-led (because the buyer is a CIO), consider a self-serve
  consumer-style wedge; or vice versa.
- `horizontal_vs_vertical` — niche bet. If the obvious play is a generic
  horizontal tool, consider going deep in one vertical first. Or vice
  versa.
- `consumer_vs_team` — collaboration bet. If the obvious play is
  collaborative team software, consider a single-user tool with no auth;
  or vice versa.
- `tool_vs_workflow` — abstraction bet. If the obvious play is a
  point tool, consider an end-to-end workflow product (or a CLI / API
  primitive). Or vice versa.

Pick the three axes that most meaningfully partition the design space
for *this specific* opportunity. If none of the listed axes fit, pick
the closest three — do not invent new axes (the menu is the prompt's
source of truth; if it needs growing, that's a prompt edit, not an
agent freelance).

# Output structure — `IdeationOutput`

Produce one JSON object with these top-level fields. Field names match
the persisted schema exactly.

- **`schema_version`** — always `"1.0"`.
- **`investigation_id`** — the UUID of the investigation that triggered
  this run. It is in your initial context.
- **`guidance`** — the optional human steering string from the re-ideate
  trigger. Empty string on the first ideation. It is in your initial
  context; copy it verbatim.
- **`generated_at`** — ISO 8601 timestamp when you produce the output.
- **`concepts`** — a JSON array of **exactly three** `Concept` objects
  (see below), with three distinct `bet_axis` values.
- **`ideation_notes`** — one paragraph of meta-commentary on the three
  concepts: what is the cross-cutting risk, what assumption do they all
  share, what would have to be true for all three to fail. This is
  separate from any single concept's `kill_criteria` — it is the
  ideation-level reflection.

## Each `Concept`

- **`name`** — a short product name, 1–4 words. Real-sounding, not
  marketing-speak ("AgentCockpit" beats "AI Operations Platform").
- **`bet_axis`** — one of the menu values above. The three concepts'
  axes must be distinct.
- **`one_liner`** — one sentence describing what the product is and who
  it's for. Concrete buyer + concrete capability. "Operator GUI for
  non-technical AI-agent power users" beats "AI platform for businesses."
- **`core_features`** — 3–6 strings. The capabilities that define this
  concept. Each one is a thing you would build, not an adjective.
- **`explicitly_not_included`** — 2–5 strings. The capabilities you are
  *consciously declining* to include. This defines the product's edges.
  Don't pad with strawmen.
- **`buyer`** — one sentence: who pays, what role, what context.
  "Marketing agency founder running OpenClaw in production with 3-10
  agents" beats "small business owner."
- **`rough_pricing_hypothesis`** — a string with a price range and a
  pricing unit ("$49-99/mo per team", "$199 one-time per seat",
  "free + $20/mo for the hosted version"). If the buyer would never
  pay (because this is open source by design), say so explicitly.
- **`competitive_landscape`** — a JSON array of `CompetitorEntry`
  objects (see below). 2–6 entries. Include direct competitors and
  closely-adjacent products. Empty list is allowed only if you searched
  and genuinely found nothing — and that warrants a note in
  `ideation_notes`.
- **`mvp_scope`** — a `MvpScope` object (see below).
- **`first_validation_test`** — one sentence describing the *concrete*
  next action that would falsify or strengthen this concept. Not "talk
  to users" but "build the approval-queue-only version against OpenClaw
  in two weeks, give it to the original HN poster, ask if they'd pay
  $49/mo for it as-is."
- **`kill_criteria`** — 2–5 strings. Each one is a falsifiable
  condition under which this concept is dead. "Original poster declines
  at $19/mo" is a kill criterion. "Users don't like it" is not. These
  should be things you could actually check within the first few weeks
  of work.
- **`fit_to_builder`** — a `FitToBuilder` object (see below). Assume
  the builder is one person with a small distribution surface.

## Each `CompetitorEntry`

- **`name`** — product / project / company name.
- **`url`** — link from a tool result, or null if you couldn't find one.
- **`positioning`** — one sentence: who they target and what they offer.
- **`overlap`** — one sentence: how their offering overlaps with this
  concept's. "Direct head-to-head", "Partial — they do X but not Y",
  "Adjacent — same buyer, different problem".
- **`threat_level`** — `"low"` | `"medium"` | `"high"`. Defensible from
  the evidence; not a guess.
- **`evidence`** — one sentence pointing at what made you assign that
  threat level. "Pricing page targets dev teams not operators", "Repo
  has had no commits in 14 months", "ProductHunt comments complain about
  the exact gap this concept fills". Cite specifics.

## `MvpScope`

- **`build_size`** — `"S"` (a weekend / a few days), `"M"` (a couple of
  weeks), or `"L"` (a month or more). T-shirt sizes only. The honest
  content is in the assumptions.
- **`build_estimate_assumptions`** — one or two sentences listing the
  assumptions behind the size. "One solo full-stack builder, no prior
  work on agent orchestration internals, integrating against one
  framework's existing API only." If the size is `S`, defend it; `S`
  estimates are the most often wrong.
- **`minimum_features_for_test`** — 2–5 strings. The exact capabilities
  the v1 must include to be a real test of the concept. If `auth` is in
  this list, every hour spent on `auth` is an hour not spent on the
  thing being tested — be ruthless.
- **`explicitly_deferred_to_v2`** — 2–5 strings. Capabilities that
  would obviously be nice but are being cut from v1. Real cuts, not
  strawmen.

## `FitToBuilder`

Assume the builder is one person with: a small distribution surface
(HN, niche subreddits, Twitter/X, a small mailing list), no sales team,
no marketing budget, no engineering team, modest hosting budget.

- **`distribution_fit`** — one or two sentences: can this concept reach
  its buyers through the builder's available channels? If it needs
  outbound sales to a CIO, say so.
- **`skill_fit`** — one or two sentences: can a solo full-stack
  generalist ship this? If it needs ML infra, real-time systems
  expertise, or a security audit, say so.
- **`capital_fit`** — one or two sentences: does the v1 require
  meaningful capital (paid APIs, paid third-party services, hosting at
  scale)? If yes, name the spend; if no, say so.

# How to call `record_ideation`

When you have everything, call `record_ideation` exactly once. The
tool's input shape is the `IdeationOutput` object above. The loop will
intercept the call, persist the output to the Ideation row, and end the
run.

If your output fails schema validation (count != 3, axes not distinct,
required fields missing), the tool will return `validation_failed` with
the reason — fix it and call again.

# Format

The `record_ideation` tool input is a JSON object whose fields are
exactly the names listed in "Output structure" above. Strings are
strings, lists are JSON arrays of strings or objects as appropriate.
Do not wrap the JSON in extra keys, code fences, or commentary.
