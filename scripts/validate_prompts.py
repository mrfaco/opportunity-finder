"""Validate every prompt file under ``prompts/``.

Rules:

* The file parses as UTF-8 markdown.
* It begins with YAML frontmatter (``---`` ... ``---``).
* Frontmatter parses as a mapping (i.e. a YAML object, not a list/scalar).
* Frontmatter has ``schema_version`` (string) and ``description`` (non-empty).
* The body is non-empty (at minimum a TODO marker — empty files would
  silently produce empty system prompts at runtime).

Run via pre-commit or directly::

    python scripts/validate_prompts.py

Exits non-zero on any violation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROMPTS_DIR = Path("prompts")


def _check(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not valid UTF-8: {exc}"]

    if not text.startswith("---"):
        errors.append(f"{path}: missing YAML frontmatter (must start with ---).")
        return errors

    end = text.find("\n---", 3)
    if end == -1:
        errors.append(f"{path}: frontmatter has no closing ---.")
        return errors

    header = text[3:end].strip()
    body_start = text.find("\n", end + 4)
    body = text[body_start + 1 :].strip() if body_start != -1 else ""

    try:
        parsed = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        return [f"{path}: frontmatter YAML invalid: {exc}"]

    if not isinstance(parsed, dict):
        errors.append(f"{path}: frontmatter must be a YAML mapping, got {type(parsed).__name__}.")
        return errors

    schema_version = parsed.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        errors.append(f"{path}: frontmatter must declare a non-empty 'schema_version' string.")

    description = parsed.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{path}: frontmatter must declare a non-empty 'description' string.")

    if not body:
        errors.append(
            f"{path}: prompt body is empty (a TODO stub is fine, but the body cannot be blank)."
        )

    return errors


def main() -> int:
    if not PROMPTS_DIR.exists():
        print(f"{PROMPTS_DIR} not found.", file=sys.stderr)
        return 2
    all_errors: list[str] = []
    for path in sorted(PROMPTS_DIR.rglob("*.md")):
        if path.name == "README.md":
            continue
        all_errors.extend(_check(path))
    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        print(
            f"\n{len(all_errors)} prompt-validation error(s). "
            "See prompts/README.md for the frontmatter contract.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
