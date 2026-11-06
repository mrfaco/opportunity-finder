# Prompts

This directory is the **source of truth** for every prompt the system uses.
Prompts are managed in git, not in the database. To change a prompt: edit the
file, commit, deploy.

## Layout

```
prompts/
├── investigation/
│   ├── system.md       # Role, goal, strategy, termination
│   └── procedural.md   # Seven-question rubric, brief structure, evidence rules
└── filter/
    └── classifier.md   # Binary opportunity classifier prompt
```

Discovery prompts live in `finder/`, `validator/` and `judge/`. Judge is the
commercial-merit gate after Finder/Validator agreement; a no-go sends the
agents to find a new opportunity. Each role has a `system.md` and may have a
`procedural.md` (Finder's research procedure, Validator's falsification
checklist, Judge's review procedure). `discovery.prompts.role_prompt` appends
the procedural body to the system body after a blank line; that effective
prompt is what the model sees, and its hash (the canonicalization below,
applied to the combined content) is the one every discovery snapshot, turn and
quality comparison records. Without `procedural.md` the effective hash equals
the system hash. Each effective prompt is capped at 2,400 words by
`tests/test_role_prompts.py`, and every per-kind table has one row per
business-model kind except `other`.

The directory name is the `agent_name`. The filename (without extension) is the
`kind`. The loader (`agents.prompts.load_prompt`) reads
`prompts/<agent_name>/<kind>.md`.

## Frontmatter

Each file MAY begin with YAML frontmatter. At minimum it should carry:

```yaml
---
schema_version: "1.0"
description: One-line description of this prompt's purpose
---
```

Frontmatter is parsed but not sent to the model. Add additional keys freely;
the loader returns them as a dict on the resulting `Prompt` object.

## Hashing — canonicalization rules

Prompt content is hashed with sha-256 over its **canonical form**:

1. Trailing whitespace is stripped from each line.
2. Line endings are normalized to `\n`.
3. Leading and trailing whitespace is stripped from the document as a whole.

The raw file content is what the model sees. The canonical form is only used
for hashing, so that cosmetic changes that don't affect model behavior don't
invalidate eval-set / run identity.

## What does the database store?

Per discovery, `prompt_snapshot[role]` captures the effective prompt's
`{schema_version, content, hash, path, parts}` (schema 1.1; stored 1.0 entries
have `content`, `hash` and `path` only).

Per `AgentRun`, the `config_snapshot.prompts` field captures `{content, hash, path}`
for every prompt used in that run. This means a run remains fully reproducible
even after prompts on disk change. Eval runs are keyed by `prompt_hash`, so two
commits with identical canonical content produce identical hashes and are
correctly identified as the same prompt for evaluation purposes.

## What about the admin?

There is a **read-only** prompt inspector at `/admin/agents/prompts/`. It
lists every file under this directory, shows the current hash, the
frontmatter, and the rendered markdown body. It is for inspection only — there
is no edit form.
