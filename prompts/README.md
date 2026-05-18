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
