"""Effective discovery role prompts: the system body plus an optional procedural body.

Both runtimes take one system prompt, so a role's procedure is appended to its
system prompt and the pair is identified by one hash. A role without
``procedural.md`` has a single part whose hash equals the system prompt's.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel

from agents import prompts as agent_prompts

PROMPT_SNAPSHOT_SCHEMA_VERSION = "1.1"


class PromptPart(BaseModel):
    kind: Literal["system", "procedural"]
    path: str
    hash: str
    schema_version: str


class RolePrompt(BaseModel):
    role: str
    content: str
    hash: str
    parts: list[PromptPart]

    def snapshot(self) -> dict[str, Any]:
        """``Discovery.prompt_snapshot[role]`` at schema 1.1.

        ``path`` stays the system file so readers of 1.0 entries keep working.
        """
        return {
            "schema_version": PROMPT_SNAPSHOT_SCHEMA_VERSION,
            "content": self.content,
            "hash": self.hash,
            "path": self.parts[0].path,
            "parts": [part.model_dump() for part in self.parts],
        }


def _part(prompt: agent_prompts.Prompt) -> PromptPart:
    return PromptPart(
        kind="system" if prompt.kind == "system" else "procedural",
        path=prompt.path,
        hash=prompt.hash,
        schema_version=str(prompt.frontmatter["schema_version"]),
    )


def role_prompt(role: str) -> RolePrompt:
    system = agent_prompts.load_prompt(role, "system")
    loaded = [system]
    content = system.content
    if (agent_prompts.PROMPTS_DIR / role / "procedural.md").exists():
        procedural = agent_prompts.load_prompt(role, "procedural")
        loaded.append(procedural)
        content = f"{system.content.rstrip()}\n\n{procedural.content.lstrip()}"
    digest = hashlib.sha256(agent_prompts.canonicalize(content).encode("utf-8")).hexdigest()
    return RolePrompt(
        role=role, content=content, hash=digest, parts=[_part(prompt) for prompt in loaded]
    )
