# Finder–Validator prototype verification

Verified locally on 2026-11-03 with Python 3.13, PostgreSQL 16 + pgvector,
Redis 7, Django, and the OpenRouter batch transport.

## Completed checks

- Offline end-to-end dialogue with real PostgreSQL and an explicitly registered
  fixture connector: Finder service proposal → Validator saturation objection →
  Finder niche pivot → acceptance of that exact proposal revision.
- Eight model operations exercised asynchronous batch submission, saved IDs,
  release/resume and polling through `httpx.MockTransport`. No billed model call
  was made. The fixture report is clearly labelled synthetic.
- Focused persistence checks covered mail read versus task completion, duplicate
  claims/submissions, successful independent Validator research, turn ceilings,
  cancellation and unsettled remote-batch reporting.
- Free DuckDuckGo search and Example Domain page fetch succeeded; local,
  metadata and non-HTTP targets were refused.
- Real browser checks: discovery list, start form, dialogue and rendered report,
  Markdown attachment download; zero console/page errors. Temporary QA users
  were deleted. A separate local prototype account is retained for review.
- Ruff lint/format, mypy, prompt validation, scoped exception discipline, Django
  system checks, and migration synchronization passed.

## Review locally

Web: `http://127.0.0.1:8088/admin/`. The local development account is stored in
the ignored, owner-readable `.tmp/prototype-login.txt`; credentials are not part
of this repository. PostgreSQL and Redis run in dedicated local containers;
Django, a solo Celery worker and Celery Beat run on the host. Other projects'
services and snapshots were not changed.

Repeat the provider-free scenario with:

```sh
.venv/bin/python scripts/smoke_discovery.py
```

The script leaves its synthetic record and `.tmp/discovery-smoke-report.md`
available for inspection. It uses the real development database, not a mocked
ORM. Do not confuse fixture conclusions with actual opportunity research.

## Remaining acceptance and hardening

- A live paid Finder–Validator discovery has not been run. The model ID,
  tool capabilities and pinned provider pricing were checked against OpenRouter's
  public catalog; the batch payload follows its current API documentation.
- Batch inference can queue for hours. The configurable default deadline remains
  1,800 seconds; choose a longer duration deliberately for live batch research.
  There is no synchronous-model fallback.
- Local cancellation stops subsequent work, while a submitted remote batch may
  still be billed. Reports preserve its ID and unsettled charge status for
  operator reconciliation. Unknown billed submissions are never blindly retried.
- Tavily requires its own key. This local checkout explicitly selects the free
  DuckDuckGo connector; free search availability/rate limits can affect research.
- Extensive regression coverage, distributed failure-injection tests and the full
  coverage gate are deferred per the user's prototype-first instruction. No
  committed coverage threshold was reduced. This is a development prototype,
  not a completed production-hardening release.


## Claude Code provider upgrade (2026-11-03)

The newer user instruction supersedes batch-only execution for new discoveries.
Config schema 1.1 selects the Claude Code harness with independent provider/model
profiles for Finder and Validator. Choices are Anthropic, OpenRouter and Ollama;
historical schema-1.0 batch snapshots continue on their original path.

Focused verification additionally passed:

- Real installed Claude Code 2.1.317 performed a four-turn discovery with actual
  MCP calls and twelve simulated OpenRouter Messages responses. Finder reused
  its session; each Validator review used a new session. Real PostgreSQL stored
  the challenge, revised service proposal, agreement, mail and readable report.
- The gateway used an ephemeral child credential, exact model routing, bounded
  requests/responses and accounted calls before returning them to Claude.
- Anthropic and Ollama provider preflight/launch contracts passed with no
  OpenRouter credential and without paid generation, login or model downloads.
- The older eight-batch offline demonstration still passed, confirming historical
  execution compatibility.

No real paid inference was run. Live Anthropic/Ollama inference and broad failure
injection/coverage remain outside these focused prototype checks. Updated setup
and execution decisions are documented in `docs/discovery.md`.


The updated browser check also passed both provider selectors (all three choices
on each role), interactive model defaults, frozen execution profiles, rendered
report and Markdown download with zero console/page errors. Temporary QA users
were removed. Large MCP result projection was checked to stay below the client's
file-spill threshold without changing stored evidence. Retry count is explicitly
zero; CLI output and research limits remain separate from the dialogue limit.
