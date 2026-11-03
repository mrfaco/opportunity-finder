# Finder–Validator implementation plan

Date: 2026-11-03
Status: prototype implemented; focused smoke checks complete, broad hardening deferred.
Specification: `../specs/2026-11-03-finder-validator-design.md`.

## Delivery strategy

Build one complete vertical slice using the current Django, Celery and
PostgreSQL stack. Do not rewrite the existing ingestion application or couple
new discoveries to clusters. Prioritize a working end-to-end prototype; use
focused smoke checks while wiring each step together. Do not build extensive
test coverage or make coverage percentages a prototype milestone. Use fake
model/provider responses for local checks; live paid runs require separate
explicit authorization and a budget.

The user explicitly deferred extensive testing until the prototype works. This
overrides AGENTS.md’s same-commit test requirement for this feature during
prototyping. Keep module-level absolute imports, git-managed versioned prompts
and loud failures. Use real PostgreSQL + pgvector for persistence smoke checks.
Do not lower the committed coverage threshold; defer new-code coverage gates
and the exhaustive test suite to step 8. All “Deferred tests” below are a
hardening backlog, not prerequisites for completing their implementation step.

## 1. Contracts and ownership

Files: new `discovery/schemas.py`, `discovery/apps.py`, `discovery/__init__.py`,
`docs/discovery.md`; narrow updates to `AGENTS.md` and `CLAUDE.md`.

- Define role, outcome, lifecycle, task/operation states and stop-reason enums.
- Define versioned request/config, proposal, validation, mail, evidence and
  final-report schemas with strict validation.
- Make proposal schemas business-model agnostic: offering, B2B/B2C/mixed/other
  customer relationship, revenue and delivery/production hypotheses, resources
  and operating assumptions. Do not require software features or subscriptions.
- Encode default 20 turns, minimum 2, independent call limits, and budget/time
  settings from the spec. Freeze resolved config, not mutable settings lookups.
- Specify stable objection IDs, proposal hashes and exact-revision acceptance.
- Keep new persistence owned by `discovery/orchestrator.py`,
  `discovery/mail.py`, `discovery/loop.py` and the research operation service;
  connectors and model tool handlers return data rather than writing records.
- Amend the old “one agentic step” rule only for Finder/Validator discoveries.

Deferred tests (step 8): schema rejection of unknown fields/versions, malformed references,
invalid limits, empty objectives and incomplete acceptance claims. Assert
counterproposals cannot masquerade as an accepted Finder revision.

Done: the contracts describe every handoff and terminal outcome without relying
on free-text interpretation.

## 2. Persistence, mail and transactional dispatch

Files: `discovery/models.py`, generated `discovery/migrations/`,
`discovery/mail.py`, `discovery/orchestrator.py`, `discovery/tasks.py`,
`config/settings.py`, `pyproject.toml`.

- Create Discovery, turns, tasks, messages, immutable revisions, evidence,
  research operations and durable dispatch records as specified.
- Add database uniqueness/check constraints for turn numbers, operation keys,
  one active task, valid limits, and discovery-scoped references validated by
  the service layer. Index inbox, pending dispatch and active leases.
- Implement idempotent discovery start and mail `send/inbox/show/log` services.
- Implement claim/renew/reclaim with fencing tokens; cancellation invalidates
  active leases and prevents subsequent commits.
- Atomically store initial human request + first task + dispatch intent.
- Publish after commit; add a repair dispatcher for unacknowledged outbox rows.
  Broker errors keep dispatch pending and visible.
- Include `discovery` in packaging, installed apps and coverage sources.

Deferred PostgreSQL tests (step 8): idempotent start, conflicting payload keys, mail read
without completion, cross-discovery replies, duplicate delivery, concurrent
claims, stale lease commits, cancellation races and crash after commit before
publication. Exercise nested transactions to prevent the existing queue-before-
outer-commit problem from entering the new workflow.

Done: a dummy Finder/Validator exchange survives worker interruption and broker
unavailability without losing or duplicating a turn.

## 3. Connector contract and general web research

Files: `discovery/connectors/base.py`, `registry.py`, `web.py`,
`discovery/research.py`, configuration additions in `config/settings.py` and
`.env.example`.

- Implement describe/search/fetch/configuration validation and explicit
  allowlisted registration. Unknown connector IDs/filters raise typed errors.
- Build Tavily web search from the existing integration, normalized result
  pagination and evidence provenance.
- Build streaming bounded page fetch with redirect/DNS/IP checks, supported
  content validation and readable text extraction.
- Keep source metadata and exact captured text/hash; distinguish snippets
  from fetched evidence. Validate quotations against stored captures.
- Record requests, reservations, usage and failure outcomes without secrets.
- Implement scoped caching, shared throttling and named bounded read retries.
  Unknown billed outcomes must remain unresolved until operator action.

Deferred tests (step 8): `httpx.MockTransport` success/error/timeout/429 cases, pagination/caps,
missing keys, unsupported filters, source normalization, byte caps, redirects
to private networks, address rebinding, credential redaction and cache scope.

Done: an agent can choose arbitrary web queries and retrieve attributable
evidence without first ingesting or clustering anything.

## 4. Existing-source connectors and extension demonstration

Files: `discovery/connectors/hacker_news.py`, `github.py`,
`stack_exchange.py`, `local_corpus.py`; selective extraction of reusable pure
research functions from `agents/tools/stubs.py` only where needed.

- Wrap existing search/fetch research logic without duplicating network clients
  or changing legacy `SourceAdapter` behavior.
- Implement read-only local-corpus search with references to original sources.
- Expose capability/configuration metadata and connector-specific typed filters.
- Add a fixture-only connector through the registry to demonstrate extension
  without editing the agent loop. Document the recipe in `docs/discovery.md`.

Deferred tests (step 8): contract tests shared across connector implementations, pagination and
authentication behavior, source-specific IDs, disabled capability exclusion,
and a discovery using the fixture connector. During prototyping, run only
focused existing checks warranted by changes to shared research functions.

Done: general search is the default; HN, GitHub, Stack Exchange and local records
are optional research sources with the same interface.

## 5. Finder and Validator turns

Files: `prompts/finder/system.md`, `prompts/validator/system.md`,
`discovery/runtime.py`, `discovery/tools.py`, `discovery/loop.py`.

- Write role prompts with version/description frontmatter. Finder proposes and
  revises; Validator independently seeks counterevidence and suggests pivots.
  Both consider services, manufacturing, physical goods, intelligence, software
  and other models without a SaaS preference. Validator applies economics and
  feasibility checks appropriate to the proposed business and customer type.
- Use OpenRouter’s asynchronous batch API with the user-selected
  `deepseek/deepseek-v4.1-flash:batch` for both independently configurable roles. Do not route these roles through the cheap filter
  dispatcher or require a legacy AgentRun/Cluster.
- Build each role's exact context from frozen prompts, objective, persisted
  handoff, proposal, objections and evidence. Save the assembled input.
- Provide only enabled read-only connector tools plus role-specific submission
  tools. Bind role/discovery/task authority on the server.
- Count internal model/research calls separately from completed dialogue turns.
- Validate citations, independent Validator research, objection responses,
  reviewed revision and verdict before a submission can finish a turn.
- Commit submission, mail, task completion, next assignment and turn count
  together under the current fencing token.

Deferred tests (step 8): scripted travel dialogue with saturation → niche pivot → acceptance;
Validator rejection followed by a new Finder candidate; adoption of a validator
counterproposal; stale revision acceptance; role spoofing; invalid submissions;
context isolation and prompt snapshots; uncited/snippet-only decisive claims;
service, manufacturing and intelligence examples that complete without SaaS
fields and receive business-specific validation.

Done: two agents exchange durable briefs and criticism through the mail service,
using research tools selected by the models.

## 6. Termination, budgets and final report

Files: `discovery/termination.py`, `discovery/reporting.py`, relevant loop/task
integration; report templates only if needed by the renderer.

- Implement exact-revision agreement and deterministic evidence minimums.
- Enforce total turn limit including odd endings, per-turn call limits, wall
  deadline, cancellation and cost reservations before each external call.
- Persist known spend and unknown outcomes distinctly, including cache usage
  and connector charges. Refuse calls without a cost upper bound.
- Fail loudly on infrastructure/schema errors and preserve partial progress.
- Render versioned report JSON and Markdown without another model agent/call.
- Make terminal report generation idempotent and repairable if interrupted.
  Store authoritative report content in PostgreSQL; downloads derive from it.

Deferred tests (step 8): default 20-turn ceiling; two-turn agreement; odd-limit unvalidated idea;
critical objection blocks acceptance; insufficient source independence; budget
and time exhaustion; cancellation before/during calls; unknown billed outcome;
crash after last submission; report retry; failed/empty runs; no invented
convergence or dropped counterevidence in rendered reports.

Done: every terminal path provides an honest, readable report with source links,
idea evolution, business/revenue model, production or service-delivery needs,
unresolved objections and next human tests.

## 7. Admin, API and documentation

Files: `discovery/admin.py`, `discovery/templates/`,
`api/serializers.py`, `api/views.py`, `api/urls.py`, `config/settings.py`,
`README.md`, `QUICKSTART.md`, `NEXT_STEPS.md`, `docs/discovery.md`.

- Add a start form for objective, constraints, limits, role models and connectors.
- Add discovery list/detail, mail thread, evidence, cost/status, cancellation and
  Markdown report download. Escape source/model text and report-derived HTML.
- Implement the spec's authenticated API endpoints with pagination, idempotent
  creation/cancel and clear running/not-ready/not-found/error responses.
- Expose safe connector configuration status without secret values.
- Make Discovery the primary sidebar/quickstart workflow and describe legacy
  ingestion separately. Document provider setup, defaults, extension contract,
  recovery/reconciliation and the meaning/limits of convergence.
- Keep existing ingestion schedules unchanged; document how an operator can
  disable them deliberately. No startup side effect disables production tasks.

Deferred tests (step 8): API/admin permissions, start validation/idempotency, thread scoping,
pagination, safe rendering, report downloads, cancel behavior and secret
redaction. During prototyping, manually smoke-check the implemented operator
flow and report download.

Done: a human can launch “travel opportunities,” inspect both agents' exchanges,
and download the final report through admin or API.

## Prototype milestone (before extensive testing)

The prototype is working when a human can start a discovery from a free-text
objective, Finder chooses web searches and submits a brief, Validator mails a
challenge, Finder responds or pivots, the configured turn limit or agreement
ends the exchange, and the human can read/download the final report. Demonstrate
connector selection and inspect persisted mail/turns. Use a short scripted run
for repeatable smoke verification; a live paid demonstration requires separate
authorization. Record obvious defects and fix blockers to this flow before
investing in broad test infrastructure, failure-injection matrices or coverage.

## 8. Post-prototype testing, hardening and release verification

Begin this phase after the prototype milestone works. Implement the deferred
test cases listed in steps 1–7, then complete the checks below. This phase is
part of the intended implementation plan, not optional scope, but must not delay
getting the first working prototype.

- Run one offline full-stack acceptance scenario with real PostgreSQL + pgvector
  and Redis/Celery, mocked providers, alternating role submissions and report
  download. Repeat with injected worker/broker failures at handoff boundaries.
- Run the full existing and new pytest suite with the repository's coverage
  configuration; retain the 84% floor or ratchet upward when justified.
- Run `ruff format --check .`, `ruff check .`, `mypy .`, exception discipline,
  prompt validation and migration synchronization. Inspect all diagnostics.
- Confirm clean installs package the new app, prompts and report assets using
  the same deployment packaging conventions as the existing project.
- Verify no historical rows or schedules changed during migration and no
  secrets/provider payloads leak into logs, UI or reports.
- Review generated reports from converged, disputed, inconclusive, failed and
  cancelled fixture discoveries for readability and factual provenance.
- Record check outcomes and known limitations in the delivery description.
  A live paid travel run is a separate, explicitly budgeted acceptance step.

## Primary implementation risks

| Risk | Required mitigation |
|---|---|
| Models agree without sufficient evidence | Exact revision, independent research, evidence minimums, explicit uncertainty |
| One weak idea consumes all turns | Objection dispositions, recorded pivots, rejected-direction memory |
| Duplicate or lost work after crashes | Atomic handoffs, uniqueness, dispatch outbox, leases/fencing |
| Call timeout leads to duplicate charges | Durable call operations and explicit unknown-outcome handling |
| Search becomes another fixed feed | Agent-chosen queries over connector capabilities |
| New feature inherits cluster coupling | Separate discovery records; leave historical AgentRun unchanged |
| Connector output controls the agent | Untrusted-content prompts, constrained tools, scoped identity |
| Report overstates commercial validation | Separate model agreement from demand proof and next human tests |

## Review assumptions before implementation

The spec proposes 20 individual responses, internal mail rather than external
email, the user-selected OpenRouter DeepSeek batch runtime, Tavily web search, USD 10 / 30-minute
default ceilings, and early completion on evidence-backed Validator acceptance
of Finder's exact revision. The prototype implements these choices; the local
configuration explicitly selects DuckDuckGo search to avoid requiring a Tavily key.
See `../../discovery.md` for setup and the remaining hardening phase.
