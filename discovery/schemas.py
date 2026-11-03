"""Versioned contracts for search-led, business-model-agnostic discovery."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash:batch"
Role = Literal["finder", "validator"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal["1.0"] = "1.0"


class DiscoveryConfig(Contract):
    max_turns: int = Field(default=20, ge=2, le=200)
    max_cost_usd: Decimal = Field(default=Decimal("10.00"), gt=0)
    max_duration_s: int = Field(default=1800, ge=1)
    max_model_calls_per_turn: int = Field(default=8, ge=1, le=50)
    max_research_calls_per_turn: int = Field(default=8, ge=1, le=50)
    max_output_tokens: int = Field(default=6000, ge=256, le=32000)
    finder_model: str = DEFAULT_MODEL
    validator_model: str = DEFAULT_MODEL
    connectors: list[str] = Field(default_factory=lambda: ["web_search", "web_fetch"])

    @field_validator("finder_model", "validator_model")
    @classmethod
    def nonempty_model(cls, value: str) -> str:
        if not value:
            raise ValueError("A role model is required")
        return value

    @field_validator("connectors")
    @classmethod
    def require_search(cls, value: list[str]) -> list[str]:
        if not {"web_search", "duckduckgo"} & set(value):
            raise ValueError(
                "A general web search connector (web_search or duckduckgo) must be enabled"
            )
        if len(value) != len(set(value)):
            raise ValueError("Connector IDs must be unique")
        return value


class EvidenceReference(Contract):
    evidence_id: str = Field(min_length=1)
    locator: str = ""
    relationship: Literal["supports", "contradicts", "context"] = "supports"
    quote: str = ""


class Claim(Contract):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    kind: Literal["fact", "inference", "hypothesis"] = "hypothesis"


class ObjectionResponse(Contract):
    objection_id: str = Field(min_length=1)
    disposition: Literal["accept", "rebut", "narrow", "pivot", "abandon"]
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class Proposal(Contract):
    title: str = Field(min_length=1)
    target_customer: str = Field(min_length=1)
    buyer: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    offering: str = Field(min_length=1)
    business_model: str = Field(min_length=1)
    customer_relationship: Literal["B2B", "B2C", "mixed", "other"]
    revenue_hypothesis: str = Field(min_length=1)
    delivery_model: str = Field(min_length=1)
    distribution_hypothesis: str = Field(min_length=1)
    alternatives: list[str] = Field(default_factory=list)
    resource_needs: str = "Unknown; requires human validation"
    operating_assumptions: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    demand_evidence_ids: list[str] = Field(default_factory=list)
    competitor_evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_tests: list[str] = Field(default_factory=list)
    parent_revision_id: str | None = None
    objection_responses: list[ObjectionResponse] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class Objection(Contract):
    id: str = Field(min_length=1)
    severity: Literal["critical", "major", "minor"]
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution: str = ""


class Validation(Contract):
    reviewed_proposal_id: str = Field(min_length=1)
    reviewed_proposal_hash: str = Field(min_length=1)
    verdict: Literal["challenge", "accept", "reject"]
    summary: str = Field(min_length=1)
    falsification_checks: list[str] = Field(min_length=1)
    objections: list[Objection] = Field(default_factory=list)
    resolved_objection_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    counterproposal: str | None = None
    next_research: list[str] = Field(default_factory=list)
    next_tests: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def acceptance_shape(self) -> "Validation":
        if self.verdict == "accept":
            if self.counterproposal:
                raise ValueError("Acceptance cannot substitute a counterproposal")
            if any(o.severity == "critical" and not o.resolved for o in self.objections):
                raise ValueError("Acceptance cannot leave critical objections unresolved")
        return self


class Report(Contract):
    discovery_id: str
    objective: str
    status: str
    outcome: str
    stop_reason: str
    turns_completed: int
    known_cost_usd: str
    estimated_cost_usd: str = "0"
    unknown_operations: int
    final_proposal: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    unresolved_objections: list[dict[str, Any]]
    limitations: list[str]
