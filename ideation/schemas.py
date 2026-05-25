"""Ideation Pydantic schema.

The agent's final output is an ``IdeationOutput`` carrying exactly three
``Concept`` entries on three distinct bet axes. The schema enforces both
the count and the distinctness so the agent cannot collapse to a single
framing dressed up three ways.

Validation failures bubble back to the agent as ``status='validation_failed'``
tool outputs (see ``agents.tools.base.Tool.dispatch``), giving the model a
self-correction signal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from core.schemas import VersionedSchema

ThreatLevel = Literal["low", "medium", "high"]
BuildSize = Literal["S", "M", "L"]


class CompetitorEntry(VersionedSchema):
    name: str
    url: str | None = None
    positioning: str
    overlap: str
    threat_level: ThreatLevel
    evidence: str


class MvpScope(VersionedSchema):
    build_size: BuildSize
    build_estimate_assumptions: str
    minimum_features_for_test: list[str] = Field(default_factory=list)
    explicitly_deferred_to_v2: list[str] = Field(default_factory=list)


class FitToBuilder(VersionedSchema):
    distribution_fit: str
    skill_fit: str
    capital_fit: str


class Concept(VersionedSchema):
    name: str
    # ``bet_axis`` is a free string rather than an Enum so the documented
    # menu in ``prompts/ideation/procedural.md`` can grow without a code
    # change. The prompt is the source of truth for legal values.
    bet_axis: str
    one_liner: str
    core_features: list[str] = Field(default_factory=list)
    explicitly_not_included: list[str] = Field(default_factory=list)
    buyer: str
    rough_pricing_hypothesis: str
    competitive_landscape: list[CompetitorEntry] = Field(default_factory=list)
    mvp_scope: MvpScope
    first_validation_test: str
    kill_criteria: list[str] = Field(default_factory=list)
    fit_to_builder: FitToBuilder


class IdeationOutput(VersionedSchema):
    """Structured ideation result — the agent's final output."""

    schema_version: str = "1.0"

    investigation_id: str
    guidance: str = ""
    generated_at: str  # ISO 8601
    concepts: list[Concept]
    ideation_notes: str = ""

    @model_validator(mode="after")
    def _three_distinct_axes(self) -> "IdeationOutput":
        if len(self.concepts) != 3:
            raise ValueError(f"ideation must have exactly 3 concepts, got {len(self.concepts)}")
        axes = [c.bet_axis for c in self.concepts]
        if len(set(axes)) != 3:
            raise ValueError(f"the 3 concepts must use 3 distinct bet_axis values, got {axes}")
        return self
