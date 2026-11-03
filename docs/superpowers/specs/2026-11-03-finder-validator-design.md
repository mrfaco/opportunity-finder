# Search-led opportunity discovery: Finder and Validator

Date: 2026-11-03
Status: prototype implemented following user authorization; broad hardening remains deferred.
Implementation plan: `../plans/2026-11-03-finder-validator-plan.md`.

## 1. Outcome and scope

A human starts a discovery with a free-text objective such as “Look for
opportunities in the travel space.” Finder researches the web, proposes a
specific opportunity, and mails its brief to Validator. Validator independently
tries to falsify the opportunity, mails its objections and promising alternative
niches back, and Finder revises or replaces the proposal. They alternate until
they converge or exhaust a configurable limit, default **20 agent turns**.

Every terminal run produces a human-readable report saying whether they
converged, what they converged on, the supporting and contrary evidence, and
what a human should investigate next. Opportunities can be services,
manufacturing, physical goods, intelligence/research businesses, software,
marketplaces, or other business models, across B2B, B2C and mixed markets.
Neither agent should default to SaaS, subscriptions, software delivery, or
venture-scale outcomes. Only explicit human constraints narrow the business
model; a sector objective such as travel does not imply a software product.
Failure and insufficient evidence are
valid outcomes; agreement between models is not market validation.

This becomes the primary discovery workflow. A discovery needs no ingested
items, embeddings, cluster, or scheduled source feed. Existing ingestion,
investigation and ideation records remain available as a separate legacy
workflow and as optional research material. Do not delete or automatically
convert them, or silently change existing schedules during migration.

Exactly two reasoning agents participate. Scheduling, mail delivery, contract
validation, termination and report rendering are deterministic application code.
No third agent decides whether the other two agreed.

First release includes a web search connector, existing searchable public-source
connectors, and a connector extension contract. It excludes general agent
teams, real email delivery, automatic outreach, autonomous product building,
subscriptions to arbitrary sources, and a new frontend framework.

## 2. Terms and operating defaults

| Setting | Proposed default / meaning |
|---|---|
| `max_turns` | 20 completed agent responses total, not 20 round trips |
| Turn order | Finder 1, Validator 2, Finder 3, Validator 4, … |
| Minimum turns | 2; larger odd limits are allowed |
| Tool/model calls | Bounded separately inside each turn; they are not dialogue turns |
| Maximum model calls per turn | 8, including the final submission |
| Maximum research calls per turn | 8 across all connectors and page fetches |
| Discovery cost ceiling | USD 10, configurable before starting |
| Discovery wall-clock ceiling | 30 minutes, configurable before starting |
| Role models | Independently configurable; `deepseek/deepseek-v4.1-flash:batch` |
| Connectors | `web_search` required and enabled by default; other connectors opt-in |
| Report | Persisted Markdown plus structured versioned JSON |

These are design defaults, not a promise that any provider completes research
within that cost or time. Freeze resolved settings and prompt hashes when the
discovery starts. Validate positive limits and reject unknown connectors or
missing required configuration before queueing work.

A turn completes only when a validated submission and its handoff are committed.
An invalid submission, retry, message read or research call does not consume an
additional dialogue turn, but its calls, elapsed time and costs still count.
If a turn cannot finish within its limits, terminate with a partial report.
The turn limit is a ceiling: agreement can finish earlier. An odd final turn
can leave a new Finder proposal unvalidated; that is not convergence.

## 3. Agent responsibilities and context

### Finder

- Translate the objective into a customer segment, recurring job, pain,
  existing workaround, buyer, differentiated offering and route to customers.
- Choose its own searches and connectors; discovery is not a fixed query feed.
- Read actual source material, not just search snippets, for decisive claims.
- Start with one best candidate rather than an unranked list of ideas.
- Address Validator's objections individually: accept, rebut with evidence,
  narrow, pivot, or abandon. A change to the customer, pain, business model or offering
  creates a new proposal revision requiring validation.
- Preserve useful rejected directions and avoid repeatedly proposing a
  candidate already ruled out without new evidence.

### Validator

- Seek disconfirming evidence independently rather than polish Finder's pitch.
- Test demand, existing competitors and substitutes, segment accessibility,
  willingness to pay, feasibility and distribution. Evaluate the proposed
  business on its own economics: delivery labor/capacity for services; tooling,
  suppliers, minimum orders, inventory and logistics for manufacturing; data
  access, freshness and defensibility for intelligence; acquisition and buying
  behavior appropriate to B2B or B2C. Record relevant capital, operating and
  regulatory constraints without requiring SaaS metrics for every proposal.
  Distinguish a weak search
  result from evidence that no competitor exists.
- Read the sources behind important claims and perform at least one independent
  research call per validation turn. Re-fetching the supplied URL alone is not
  independent research.
- Return `challenge`, `accept` or `reject`, explicit objections with severity,
  evidence, and the next most useful test. Suggest a narrower niche or a new
  direction when supported; label speculative suggestions as hypotheses.
- `accept` applies only to the exact Finder proposal revision under review.
  A promising alternative in a challenge is not an accepted opportunity.

Both roles receive the initial objective and constraints, their own git-managed
role prompt, the latest proposal, outstanding objections, prior decision
summaries, shared evidence references, and the relevant mailbox thread. Finder
and Validator have separate model histories. Validator starts each turn with a
fresh context assembled from persisted inputs; it does not inherit Finder's
private reasoning. Save the exact assembled inputs for auditability. Bound
history by passing structured summaries and references; never silently truncate
the current proposal or unresolved objections.

Prompts instruct both roles to treat downloaded text as evidence, never as
instructions, to distinguish facts from inferences, and to avoid fabricated
citations, fake market-size precision or pressure to agree near the turn limit.

## 4. Durable mail, inspired by Video Swarm

Reference implementation inspected:

- `video-swarm/src/vswarm/mail.py`: request/task transaction, read versus done,
  reply threading, sender identity and duplicate handling.
- `video-swarm/src/vswarm/db.py`: messages, tasks, leases and results.
- `video-swarm/docs/superpowers/specs/2026-10-20-video-swarm-design.md`.

Use those semantics with Django/PostgreSQL; do not import Video Swarm or share
its database. “Mail” means internal persisted messages, not SMTP or messages
to people or external accounts.

`DiscoveryMessage` contains version, discovery, sender, recipient, kind
(`request`, `reply`, `note`), subject, readable body, artifact revision reference,
reply-to, task reference, creation time and read time. Parties are `human`,
`system`, `finder` and `validator`. Model tool calls cannot choose their sender;
the server supplies it from the leased turn. Reject cross-discovery references.

The initial human request creates Finder's first task. Finder's committed brief
creates a Validator request. Validator's challenge/rejection completes its task
and creates a Finder request. Each subsequent handoff replies to the preceding
message and references the exact immutable submission. A terminal acceptance
creates a reply and a human-visible completion note without another agent task.

A request and its task are one transaction. Submission, task completion,
counterparty request, turn count and next-state change are also one transaction.
Reading mail never completes a task. There is one active dialogue task per
discovery, and one committed submission per turn number. Repeated delivery or
submission with the same operation key returns the original result; a different
payload for that key is a conflict. Enforce uniqueness in the database.

Celery dispatch happens after the outermost commit through a durable dispatch
record. A periodic dispatcher repairs committed but unpublished work. An
`on_commit` callback can prompt dispatch but is not the only delivery mechanism.
The worker claims a task with a renewable lease and fencing token. A stale
worker cannot commit a reply after another worker reclaimed or cancelled it.
No database transaction stays open while a model or connector runs.

## 5. State, convergence and recovery

Discovery lifecycle: `queued → running → completed | failed | cancelled`.
Completion outcome is separate: `converged`, `not_converged`, or `inconclusive`.
Stop reasons include `agreement`, `turn_limit`, `cost_limit`, `duration_limit`,
`turn_call_limit`, `provider_error`, `invalid_submission`, `worker_lost` and
`human_cancelled`.

Convergence requires all of:

1. Finder has committed a concrete proposal revision and asks to validate it.
2. Validator accepts that same revision, without a modified candidate.
3. No unresolved critical objection remains. Every previously raised critical
   objection has a disposition, rationale and evidence references.
4. The evidence requirements below pass deterministic validation.

The first release requires a cited demand signal, a cited competitor/substitute
comparison, a specific segment and wedge, and Validator's independent research.
Require at least two distinct source origins across the decisive evidence;
syndicated copies count as one origin. These checks establish an auditable
research minimum, not truth or commercial viability. The report must still
state the untested assumptions and recommend customer validation.

Validator can conclude that a candidate is invalid and suggest no alternative;
Finder then explores elsewhere within the original objective. Exhausting turns
with unresolved objections yields `not_converged`. Missing essential evidence,
or stopping on an unreviewed proposal, yields `inconclusive`. Infrastructure
failure yields lifecycle `failed`, an inconclusive partial report, and a visible
error. Never translate a provider failure into “no opportunities found.”

After every committed turn persist enough state to resume without rerunning
completed turns. A lease expiry before any external call can be retried. Save
an operation record before every billed call and its response/usage as soon as
available. A timeout with an unknown billed outcome is held for explicit
operator reconciliation or a knowingly authorized new attempt; do not blindly
repeat it. Report only known spend and separately flag unknown charges.
Known-unbilled reads may use bounded retries for 429, 5xx and transport errors.

Check cancellation, deadlines and available budget before every external call
and before committing a turn. Apply per-call timeouts. A hard spend ceiling is
enforceable only when provider cost is known or a conservative upper bound can
be reserved; refuse a call without that bound. Record model tokens including
cache writes/reads and connector charges in one discovery ledger. A returned
response exceeding its reservation stops further calls and exposes the overrun.

## 6. Contracts and data model

Introduce a `discovery` Django app; do not manufacture a Cluster to satisfy the
existing non-null `AgentRun.cluster` field. Keep the historical agent tables
unchanged in this release. New orchestration owns new discovery records and
can reuse model client, prompt loading and provider helpers where suitable.

| Record | Purpose |
|---|---|
| `Discovery` | Objective, constraints, config snapshot, lifecycle/outcome, counters, report |
| `DiscoveryTurn` | Role, sequence, task/lease, exact inputs, submission, model/prompt, timings |
| `DiscoveryTask` | Durable assignment, state, attempts, lease and fencing token |
| `DiscoveryMessage` | Threaded internal mail with immutable artifact references |
| `ProposalRevision` | Immutable candidate, parent revision, changed assumptions and response to objections |
| `ValidationRevision` | Reviewed proposal, verdict, objections, counterproposal and tests |
| `ResearchOperation` | Planned/started/completed/unknown call, reservation, request/response metadata |
| `Evidence` | Connector, canonical URL/external ID, captured text/hash, timestamps and provenance |
| `DiscoveryDispatch` | Transactional outbox for task publication |

All tool I/O, submissions, evidence payloads, mail envelopes and reports declare
`schema_version`, initially `1.0`, and reject unknown fields. Store schema
definitions in git. Future incompatible versions need an explicit migration
path; never reinterpret old briefs with the newest schema.

Proposal fields include title, target customer/buyer, problem, supporting claim
IDs, current alternatives, differentiated offering, business model, customer
relationship (B2B/B2C/mixed/other), revenue hypothesis, delivery/production model,
distribution hypothesis, indicative resource/capital needs, operating assumptions,
risks, next tests, parent revision and objection responses. Business-model values
are descriptive and extensible, not an enum restricted to software categories.
Unknown economics are labeled assumptions rather than invented estimates. Validation fields
include reviewed proposal ID/hash, verdict, falsification checks, stable objection
IDs, severity, evidence, resolutions, optional counterproposal, and next research
instructions. A counterproposal must be adopted into a Finder revision before
it can be accepted.

Evidence distinguishes `search_snippet`, `fetched_page`, `source_record` and
`internal_record`. Claims reference evidence IDs and locators, with a stated
relationship (`supports`, `contradicts`, `context`). Asserted quotations must
match captured text. Search snippets are discovery leads and cannot alone
satisfy decisive demand/competitor evidence. Save retrieval time separately
from publication time; record unavailable or inaccessible sources explicitly.

## 7. Data connectors

A connector is a read-only research capability, not an ingestion scheduler and
not an agent. Define a small typed Python interface:

- `describe()`: stable ID, version, capabilities, configuration requirements,
  supported filters, access limitations and cost policy.
- `search(query, filters, cursor, limit)`: normalized result page with next cursor.
- `fetch(external_id_or_url)`: normalized evidence document when supported.
- `check_configuration()`: local configuration validation; no paid probes.

Normalized results carry schema version, connector/version, stable external ID,
canonical URL, title, excerpt, author/publication time where known, retrieval
time, source metadata and provenance. A page includes truncation/continuation
information. Never present a capped result set as exhaustive coverage. Declare
unsupported operations and filters rather than silently ignoring them.

Initial connectors:

| ID | Capability / implementation |
|---|---|
| `web_search` | General web search through the existing Tavily integration |
| `web_fetch` | Bounded page retrieval for search results and cited URLs |
| `hacker_news` | Search/fetch using existing research tool logic |
| `github` | Search/fetch public issues using existing research logic |
| `stack_exchange` | Search/fetch using existing research logic |
| `local_corpus` | Read-only search of this application's stored cluster/items |

Wrap reusable research functions; do not force query-based search through
`SourceAdapter.fetch_new_items(since=...)`. Keep legacy ingestion adapters
intact. Future sources implement the same contract and register via an explicit
allowlisted Django setting; no dynamic code import requested by an agent.
Configuration comes from deployment settings/environment with secret references,
never credentials in a brief, snapshot, model context or report. Discovery
snapshots connector IDs, versions, non-secret options and capability schemas.

Only enabled connectors appear in agent tools. Both roles use the same configured
capabilities but choose their own queries. Unknown IDs, missing credentials and
unsupported filters fail explicitly. Expected 404/access-denied/rate-limited
results are typed, visible tool outcomes; required-provider outages stop the
run rather than silently remove its research capability.

Use shared host/credential rate limits, bounded page sizes, timeouts and a cache
keyed by connector version, query/filters and access scope. Cached evidence
retains its original retrieval time. Page fetches stream with a byte cap and
reject unsupported content. Block private, loopback, link-local and metadata
targets, including redirects and DNS rebinding; validate each connection.
Private-service connectors, if added later, need their own explicit scoped
transport rather than disabling public-fetch protections.

## 8. Report and operator surfaces

The final report is rendered deterministically from validated submissions and
evidence. Agent-written summaries can supply prose, but cannot change the
computed outcome, hide contrary evidence, or add unsupported claims. Reporting
does not consume an agent turn or another model call. Render from persisted
data on every terminal path; a report write failure remains visible and retryable.

Required sections:

1. Outcome in plain language: converged / did not converge / insufficient evidence.
2. Original objective, actual turns, elapsed time and known/unknown spend.
3. Final agreed opportunity, or strongest remaining candidate explicitly marked
   unvalidated/disputed, with customer, problem, offering, business/revenue model,
   delivery or production requirements and commercial hypothesis.
4. Evidence for and against, with source links, timestamps and locators.
5. How the idea evolved: proposals, objections, pivots and rejected directions.
6. Remaining risks, unresolved objections and evidence gaps.
7. Recommended next human tests and what would falsify the recommendation.
8. Stop reason, connector coverage/failures, configuration and provenance appendix.

Provide a Discovery admin start form, status list, detail page, threaded mail,
turn/evidence inspection, cancel action and Markdown download. Add Discovery to
the primary sidebar; label historical screens as the legacy ingestion workflow.
Reuse API-key authentication and orchestrator-backed DRF views:

- `GET/POST /api/v1/discoveries/` (list/start, with idempotency key on start).
- `GET /api/v1/discoveries/{id}/` (status, outcome, counters and error).
- `GET /api/v1/discoveries/{id}/messages/` and `/evidence/` (paginated).
- `GET /api/v1/discoveries/{id}/report/` (Markdown download).
- `POST /api/v1/discoveries/{id}/cancel/` (idempotent).
- `GET /api/v1/connectors/` (capabilities/configuration status; no secrets).

Reports requested while running return a clear not-ready response. Agents do
not get the human API's authority to start/cancel other discoveries. PDF export
and interactive mid-run steering are deferred.

## 9. Acceptance scenarios

- “Travel opportunities” produces independent searches, a Finder brief, a
  Validator challenge about saturation, a niche pivot, and an accepted exact
  revision with a cited report.
- A services, manufacturing or intelligence proposal can complete the same
  workflow and report without software features, subscriptions or SaaS metrics.
  Validator assesses its actual operating model and customer economics.
- A validator-suggested niche never appears as converged until Finder adopts
  it and Validator validates that revision.
- Twenty alternating non-accepting submissions stop at turn 20 with the actual
  disagreement preserved. A shorter/odd limit reports any unvalidated final idea.
- A duplicate Celery delivery, a crash after reading mail, and a crash between
  commit and broker publication do not lose a task or duplicate a handoff.
- Missing search credentials prevent startup. Provider errors during research
  yield a failed partial report rather than “no opportunity.”
- Budget, deadline and cancellation stop further calls and still yield reports.
- Adding a fixture connector requires no changes to the alternating loop.
- The entire dialogue can be tested offline with fake model responses and
  `httpx.MockTransport`, using real PostgreSQL for persistence and concurrency.

## 10. Compatibility and decisions

The user's requested two-agent workflow supersedes the repository rule that
only investigation may be agentic, for this new workflow. Existing rules on
exception discipline, git-managed prompts, schema versions and ownership of
persistence remain. Per the user’s explicit sequencing instruction, prioritize a
working prototype with focused smoke checks; extensive regression tests, failure
injection and coverage verification follow in the final hardening phase. This
overrides the same-commit test requirement for this feature during prototyping;
do not lower the existing committed coverage threshold. Update AGENTS.md and CLAUDE.md narrowly during
implementation to describe the new boundaries; this specification changes no
runtime behavior by itself.

Proposed choices for review: 20 means individual turns; internal durable mail;
OpenRouter DeepSeek V4.1 Flash batch runtime (user-selected); Tavily default search; exact-revision
Validator acceptance ends discussion; one final report rather than a forced
winner. Runtime provider abstraction can follow existing conventions, but a
Claude Code/Codex subprocess runtime is not part of this first release.
