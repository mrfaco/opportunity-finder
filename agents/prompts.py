"""File-based prompt loading + canonicalization + hashing.

Prompts live under ``<repo>/prompts/<agent_name>/<kind>.md``. There is no
database table for prompt content. Hashes are computed over a canonical form
so cosmetic edits do not invalidate eval identity.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from pydantic import BaseModel

PROMPTS_DIR: Path = Path(settings.BASE_DIR) / "prompts"


class Prompt(BaseModel):
    agent_name: str
    kind: str
    content: str  # Raw content sent to the model
    canonical: str  # Canonicalized form used for hashing
    hash: str  # sha256 of canonical
    frontmatter: dict[str, Any]
    path: str  # Relative to repo root


def canonicalize(content: str) -> str:
    """Canonicalize prompt content for hashing.

    Rules — see ``prompts/README.md``:
      1. Strip trailing whitespace from each line.
      2. Normalize line endings to ``\\n``.
      3. Strip leading and trailing whitespace overall.

    The model still sees the raw content; canonicalization is only for hashes.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body_start = text.find("\n", end + 4)
    body = text[body_start + 1 :] if body_start != -1 else ""
    parsed = yaml.safe_load(header) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Prompt frontmatter must be a YAML mapping, got {type(parsed).__name__}")
    return parsed, body


def load_prompt(agent_name: str, kind: str) -> Prompt:
    """Read ``prompts/{agent_name}/{kind}.md`` and return a parsed ``Prompt``.

    Raises ``FileNotFoundError`` if the file does not exist.
    """
    rel = Path("prompts") / agent_name / f"{kind}.md"
    abs_path = PROMPTS_DIR / agent_name / f"{kind}.md"
    if not abs_path.exists():
        raise FileNotFoundError(f"Prompt not found: {rel}")
    text = abs_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    canonical = canonicalize(body)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Prompt(
        agent_name=agent_name,
        kind=kind,
        content=body,
        canonical=canonical,
        hash=digest,
        frontmatter=frontmatter,
        path=str(rel),
    )


def get_prompts_for_agent(agent_name: str) -> dict[str, Prompt]:
    """Load every ``.md`` prompt for the given agent.

    Used at run start to snapshot all relevant prompts onto the run record.
    """
    agent_dir = PROMPTS_DIR / agent_name
    if not agent_dir.exists():
        raise FileNotFoundError(f"No prompts directory for agent: {agent_name!r}")
    out: dict[str, Prompt] = {}
    for md in sorted(agent_dir.glob("*.md")):
        out[md.stem] = load_prompt(agent_name, md.stem)
    return out


def list_all_prompts() -> list[Prompt]:
    """Return every prompt file under ``prompts/`` — used by the admin inspector."""
    out: list[Prompt] = []
    if not PROMPTS_DIR.exists():
        return out
    for agent_dir in sorted(PROMPTS_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        for md in sorted(agent_dir.glob("*.md")):
            out.append(load_prompt(agent_dir.name, md.stem))
    return out
