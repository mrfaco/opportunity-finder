"""Brief Pydantic schema.

Every brief carries a ``schema_version`` so we can evolve the structure
without breaking historical rows. Use ``Brief.json_schema()`` to export the
JSON Schema for the agent's final-output contract.
"""

from __future__ import annotations

from pydantic import Field

from core.schemas import VersionedSchema


class Evidence(VersionedSchema):
    source: str
    url: str
    title: str | None = None
    snippet: str
    posted_at: str | None = None  # ISO 8601


class Competitor(VersionedSchema):
    name: str
    url: str | None = None
    revenue_signal: str | None = None
    notes: str | None = None


class Brief(VersionedSchema):
    """Structured opportunity brief — the agent's final output."""

    schema_version: str = "1.0"

    headline: str
    problem_statement: str
    target_user: str
    evidence_summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_next_step: str
