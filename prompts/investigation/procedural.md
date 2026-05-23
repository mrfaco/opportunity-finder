---
schema_version: "1.0"
description: Procedural prompt — seven-question investigation rubric, brief structure, evidence rules
---

# The seven questions

Before you write the brief, you need defensible answers to these seven
questions. If you cannot answer one with specific evidence, say so
explicitly in the brief — do not paper over the gap.

1. **What exactly is the need?** Phrase it as a one-sentence problem
   statement, in the user's own framing. Not "users want better PDF
   tools" — instead "freelance bookkeepers need to merge a folder of
   client PDFs and pick custom page ranges per file without a monthly
   subscription". The specificity *is* the value here.

2. **Who feels it?** A named segment, not "users". Profession, scale,
   context. "Solo physiotherapists managing their own appointment
   reminders" beats "small healthcare practices". If the cluster items
   themselves don't make the segment clear, search externally for who is
   complaining in the same words.

3. **How acute and frequent is the pain?** How often does the user run
   into it? How costly is the current workaround? A pain that hits once
   a year for ten minutes is not a product. A pain that hits weekly for
   an hour is. Cite specifics where you can ("the original poster says
   it takes them a full afternoon every month").

4. **What do people do today?** What are the workarounds, manual
   processes, half-fitting tools, scripts, or spreadsheets? Existence of
   ugly workarounds is the strongest positive signal you can find —
   people only build a mess when no clean tool fits.

5. **What products already exist?** Name them. Note who they serve, what
   they charge, and where their users say they fall short. Empty
   competitor list is a valid output only if you genuinely couldn't find
   competitors — that itself is suspicious (mature pains usually have
   incumbents).

6. **What would a winning solution look like?** Two or three concrete
   differentiators a new attempt could plausibly offer — not "be better"
   but "one-time purchase instead of a subscription", "local-only so the
   privacy concern disappears", "supports the specific workflow the
   incumbents miss". These should fall out of what users say is missing
   in (5), not from your imagination.

7. **What's the case against?** Risks, deal-breakers, reasons to pass.
   Niche size, regulatory load, requires a network effect to be useful,
   incumbent is already moving in, distribution is hard, the workaround
   is genuinely good enough. Be honest. A brief with no risks is a brief
   you have not interrogated.

# Brief structure

Produce the brief with these fields. Field names match the persisted
schema exactly.

- **`schema_version`** — always `"1.0"` for this version of the brief
  format.
- **`headline`** — a short, specific name for the opportunity. Six to
  twelve words. "Local-first PDF merge for indie bookkeepers", not "PDF
  tooling opportunity".
- **`problem_statement`** — your answer to question 1, in one sentence.
- **`target_user`** — your answer to question 2.
- **`evidence_summary`** — a 2–4 sentence narrative that synthesizes the
  evidence you gathered: the pain (question 3), the workarounds (question
  4), and the corroboration you found outside the original cluster.
  This is the paragraph the human reviewer reads first; make it useful.
- **`evidence`** — a list of the specific items you cited. Each item:
  `source`, `url`, `title` (optional), `snippet` (the actual quoted text
  that supports a claim, kept short), `posted_at` if you have it. Aim
  for 3–8 items. Quality beats quantity — one vivid first-person account
  is worth five paraphrases. **Every item must be something a tool
  actually returned to you.**
- **`competitors`** — a list, from question 5. Each item: `name`, `url`
  if you have it, `revenue_signal` (from `query_trustmrr` or what you
  saw in coverage; null if you don't know), `notes` (one sentence on
  who they serve and what users say they miss). Empty list is allowed
  but you should explain in `evidence_summary` why you couldn't find any.
- **`differentiators`** — a list of strings, from question 6. Two or
  three is usually right. Each one is a concrete capability or model
  choice, not an adjective.
- **`risks`** — a list of strings, from question 7. At least one. If you
  truly see no risks, you haven't looked hard enough; go look.
- **`confidence`** — a float in `[0.0, 1.0]`. See the calibration
  section below.
- **`recommended_next_step`** — one sentence: what should the human
  reviewer do next? "Validate with five solo physiotherapy clinics via
  the Australian chiro forum before building anything." Not "promote
  this to the build queue" — that's the reviewer's decision, not yours.

# Evidence rules

- **Every concrete claim is tied to a citation.** If you state a number,
  a competitor name, a user quote, a workaround, or a pricing point, the
  corresponding entry in `evidence` (or `competitors`) must support it.
- **URLs come from tool results, never from memory.** If you can't paste
  the URL from a tool result, you don't have evidence — say "no external
  evidence found" rather than fabricate.
- **Quote short.** Snippets in `evidence` should be a sentence or two,
  not a paragraph. Quote the part that makes the point.
- **Cluster items count as evidence too.** Items from `query_cluster`
  are valid evidence entries — that's the whole point of the cluster.
  Don't omit them in favor of external evidence; use both.
- **Multiple independent sources strengthen confidence.** Two unrelated
  posts on different sites saying the same thing matters more than one
  long thread.
- **If a tool returned no useful result, that is itself a finding.**
  "Searched Product Hunt for {term}, no comparable products surfaced"
  is a worth-saying observation that supports a higher differentiation
  score.

# Confidence calibration

`confidence ∈ [0.0, 1.0]` is your honest probability that a human reviewer
would agree this is a real opportunity worth pursuing.

- **`0.85–1.0`** — strong, multi-source evidence of the need; clear
  segment; either a real gap in incumbents or no incumbent at all; you
  can name concrete differentiators that fall directly out of stated user
  complaints. Reserve this band for cases you would defend confidently.
- **`0.6–0.85`** — the need is real and you have evidence, but at least
  one of: the segment is fuzzy, incumbents may be adequate, the
  differentiation is speculative, or you couldn't find enough
  corroboration. Honest "yes with caveats."
- **`0.4–0.6`** — genuinely on the fence. The cluster is real but you're
  not sure it's an opportunity vs. a known-but-unfixable pain or a niche
  too small to support a product. Useful to flag for human judgement.
- **`<0.4`** — you investigated and the signal weakened. Write a brief
  anyway; the reviewer benefits from your reasoning about why this
  cluster turned out thin.

Overconfidence is more damaging than under-confidence. The reviewer
catches false positives; you cause them.

# Format

The brief is a JSON object whose fields are exactly the names listed in
"Brief structure" above. Strings are strings, lists are JSON arrays of
strings or objects as appropriate. The harness will validate the shape
against the schema and reject it if you stray; producing the fields
correctly the first time saves a retry. Do not wrap the JSON in extra
keys, code fences, or commentary — the brief object itself is the final
output.

# A worked example (sketch)

For a cluster of complaints from indie bookkeepers about manually merging
client PDFs:

- `headline`: "Local-first PDF merge for solo bookkeepers"
- `problem_statement`: "Solo bookkeepers spend hours each month manually
  merging client PDFs with custom page ranges; existing tools are either
  subscription-only or won't keep files off the cloud."
- `target_user`: "Solo and small-firm bookkeepers handling 5–30 clients,
  often regulated to keep client files local."
- `evidence_summary` (2–4 sentences synthesizing): the recurring monthly
  task, the cloud-upload deal-breaker, what people use today, who else is
  complaining outside the cluster.
- `evidence`: 4–6 items: 2–3 from the cluster, 2–3 from external
  searches that show the same complaint elsewhere.
- `competitors`: Adobe Acrobat (too expensive for solo, requires cloud
  for some features), Smallpdf (subscription, cloud), pdftk (CLI,
  unfriendly for non-devs). Each with notes on the gap.
- `differentiators`: ["one-time purchase, no subscription", "fully
  offline / local-only", "GUI for batch operations with custom page
  ranges per file"].
- `risks`: ["niche may be too small for a sustainable business",
  "established cloud players can ship a one-time-purchase tier and
  undercut", "PDF tooling is a famously crowded category".]
- `confidence`: `0.75`
- `recommended_next_step`: "Post a one-paragraph value prop to two
  bookkeeping subreddits and an accounting Discord to see whether the
  segment self-identifies."

This sketch is for illustration only — populate the actual brief from
the actual evidence the tools return.
