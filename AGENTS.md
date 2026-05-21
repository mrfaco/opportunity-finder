# AGENTS.md

Working rules for AI agents (Claude Code, Codex, Cursor, etc.) and the
humans reviewing their changes. This repo is deliberately AI-coded — the
hooks and gates in this repo exist so the review burden stays minimal.
**Read this before writing code.**

The hooks enforce most of these rules. Where a human judgment call is
needed, the rule is stated here so the agent knows what target to aim at.

## 1. Exception discipline — never swallow, never fall back

The project's first rule. Failures must be **loud**.

### What we forbid

- **Bare `except:`** or `except Exception:` that catches and does not
  re-raise. Ruff `BLE001` catches the syntax; the custom checker at
  `scripts/check_exception_discipline.py` catches the semantics (an
  `except` block that neither raises nor unconditionally exits).
- **Log and continue.** `logger.error(...)` followed by no `raise` is the
  archetype. Either re-raise after logging (`logger.exception(...)` if
  you want the traceback in the log), or don't catch.
- **Fallback values on error.** `except X: return None` / `return []` /
  `return DEFAULT` is the same anti-pattern. If the caller can't tell
  whether you got real data or a default, the bug just got harder to
  diagnose later. Raise.
- **`try` blocks that span more than the single statement that can fail.**
  Wrap precisely what raises; not the whole function body.
- **Bare `raise Exception(...)`.** Use a specific exception type. Ruff
  `TRY002` enforces this.

### What we require

- **Re-raise preserves traceback.** Use plain `raise` if you only want to
  log + propagate.
- **Translate with `from`.** When converting one exception type into
  another, use `raise NewError(...) from exc` so the original cause
  survives. Ruff `B904` enforces this.
- **`logger.exception(...)`** when logging inside an `except`. It's what
  ruff `TRY400` enforces and what gives operators a usable stack trace.
- **`# allow: suppress-exception`** on the rare `except` line where
  suppression really is the design. We grep for these during review.
  Today there are exactly two:
    * `agents/loop.py` — catching the loop's own `LoopAbort` sentinel
      (it's how we drive the structured finish path).
    * `agents/tools/base.py` — turning Pydantic `ValidationError` into a
      `validation_failed` tool output (the agent uses this signal to
      self-correct).

### How to validate locally

```sh
python scripts/check_exception_discipline.py
ruff check .
```

The pre-commit hook runs both on staged files.

## 2. Test discipline — every change has a test

- New behavior gets a test in the same commit.
- A bug fix gets a regression test that fails on the pre-fix code.
- Tests should use real Postgres + pgvector via Docker Compose. Don't
  monkey-patch your way around the DB layer in business-logic tests.
- The smoke suite (`tests/test_*.py`) is the floor, not the ceiling.

## 3. Coverage gate — ratchet up, never down

- The threshold lives in `pyproject.toml` under
  `[tool.pytest.ini_options].addopts` as `--cov-fail-under=N`.
- The pre-push hook runs the full suite with this gate.
- To raise the floor after adding tests: `make coverage-ratchet`. It
  reads the latest `coverage.xml`, takes `floor(line_rate * 100)`, and
  rewrites the threshold **only if it would go up**.
- Never lower the threshold by hand. If you absolutely must (e.g. a
  deletion drops the percentage), justify it in the PR description.

## 4. Format & lint

- `ruff format` owns formatting. Don't argue with it.
- `ruff check` runs with the rule set declared in `pyproject.toml`. New
  rule violations land as commit blockers.
- **Imports are absolute and module-level.** Use `from agents.models
  import X`, never `from .models import X` (`TID252`). Keep imports at the
  top of the file (`PLC0415`); a genuinely-deferred import — circular-import
  break, Django `ready()` side-effect — needs an explicit `# noqa: PLC0415`
  with a one-line reason.
- Migrations are excluded from lint (they are Django-generated).
- `mypy .` must be clean before push. Config in `pyproject.toml` —
  `django-stubs` plugin is enabled. Migrations + tests + scripts are
  excluded for now; the rest of the codebase has no `Any` slop.

## 5. Prompts are git-managed

- All prompt content lives under `prompts/`.
- Editing a prompt = a commit. There is no admin UI for it.
- See `prompts/README.md` for the canonicalization + hashing rules.
- Every `prompts/**/*.md` file must have YAML frontmatter declaring
  `schema_version` and `description`, plus a non-empty body. The
  pre-commit hook (`scripts/validate_prompts.py`) enforces this.

## 6. Migrations stay in sync with models

- Editing a model without running `makemigrations` is a footgun. The
  pre-push hook (`scripts/check_missing_migrations.sh`) and the
  `migrations-sync` CI job both run `manage.py makemigrations --check
  --dry-run` and fail if anything is missing.
- Don't hand-edit auto-generated migrations unless you're doing data
  surgery — and if you do, write a test that exercises the migration.

## 7. Secrets never land in git

- `gitleaks` runs as a pre-commit hook and in CI. Catches API keys,
  tokens, AWS creds, and the most common `.env` shapes.
- If you ever commit a real secret by accident: rotate the key
  *immediately*, then deal with rewriting history second. The leak hits
  the moment the commit reaches a remote, not when someone notices.

## 8. Don't add scope

- Bug fixes don't get surrounding refactors. New features come with the
  minimum surface they need. Tests yes, abstraction no.
- Don't add fallbacks "just in case." See rule 1.
- Don't write comments that restate the code. Comments explain *why*
  something is non-obvious — hidden constraints, subtle invariants,
  references to issues. If you removed the comment and a reasonable
  reader still understood the code, it shouldn't be there.

## 9. Don't bypass the hooks

- `git commit --no-verify` exists for genuine emergencies (a broken hook
  itself, a recovery commit). It is never the right answer to "the tests
  fail."
- CI runs the same gates. Bypassing locally just delays the failure.

## 10. Boundaries between modules

- **Agents and tools never write to the database directly.** The
  orchestrator (`agents/orchestrator.py`) and loop (`agents/loop.py`) are
  the only modules that write `AgentRun` / `AgentStep` / `AgentEvent`
  rows.
- **Adapters don't classify or cluster.** They emit `IngestedItem`
  records; the pipeline drives the next steps.
- **Workflows vs agents.** Anything deterministic stays in a function or
  Celery task. Only the investigation loop is an agent. Don't reach for
  agentic patterns where a workflow does the job.

## 11. Schema versioning

- Every structured artifact (briefs, tool I/O, prompt frontmatter)
  carries a `schema_version`.
- When you change the shape, bump the version and write the migration
  path in the same commit.

## 12. Logging is structured

- Trajectory data goes to the DB (`AgentEvent`), not stdout. No
  `print()` calls inside business logic.
- `logger.info` is for operator-readable lifecycle events. `logger.error`
  is for things that should page someone. `logger.exception` is the only
  thing that belongs inside an `except` block (and only when you also
  re-raise).
