"""Versioned contracts for search-led, business-model-agnostic discovery."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
LEGACY_BATCH_MODEL = DEFAULT_MODEL + ":batch"
Provider = Literal["anthropic", "openrouter", "ollama"]
Role = Literal["finder", "validator", "judge"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal["1.0"] = "1.0"


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    runtime: Literal["claude_code", "openrouter_batch"] = "claude_code"
    finder_provider: Provider = "openrouter"
    validator_provider: Provider = "openrouter"
    judge_enabled: bool = True
    judge_provider: Provider = "ollama"
    max_turns: int = Field(default=100, ge=2, le=200)
    max_cost_usd: Decimal = Field(default=Decimal("10.00"), gt=0)
    max_duration_s: int = Field(default=1800, ge=1)
    max_model_calls_per_turn: int = Field(default=8, ge=1, le=50)
    max_research_calls_per_turn: int = Field(default=8, ge=1, le=50)
    max_output_tokens: int = Field(default=24000, ge=256, le=32000)
    finder_model: str = DEFAULT_MODEL
    validator_model: str = DEFAULT_MODEL
    judge_model: str = "kimi-k3:cloud"
    connectors: list[str] = Field(default_factory=lambda: ["search_auto", "web_fetch"])
    # 1.3: "origins" is the original two-origin rule; "typed" also requires an
    # independent demand source and offer-level competitor evidence.
    evidence_policy: Literal["origins", "typed"] = "origins"
    # Minimum 1 − max Jaccard between a Validator search and every earlier
    # Finder search; 0.0 accepts any non-identical query, as before 1.3.
    min_validator_query_novelty: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def pre_policy_config(cls, value: Any) -> Any:
        """Explicit 1.0–1.2 snapshots read as ``origins`` and novelty 0.0.

        A dumped older config carries those defaults; any other value needs 1.3.
        """
        if isinstance(value, dict) and value.get("schema_version") in {"1.0", "1.1", "1.2"}:
            if value.get("evidence_policy", "origins") != "origins" or float(
                value.get("min_validator_query_novelty", 0.0)
            ):
                raise ValueError(
                    "evidence_policy and min_validator_query_novelty require config schema 1.3"
                )
        return value

    @model_validator(mode="before")
    @classmethod
    def historical_batch_config(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schema_version") in {"1.0", "1.1"}:
            migrated = dict(value)
            migrated.setdefault("judge_enabled", False)
            if value.get("schema_version") == "1.0" and "runtime" not in migrated:
                migrated["runtime"] = "openrouter_batch"
                migrated.setdefault("finder_model", LEGACY_BATCH_MODEL)
                migrated.setdefault("validator_model", LEGACY_BATCH_MODEL)
            return migrated
        return value

    @model_validator(mode="after")
    def provider_models(self) -> "DiscoveryConfig":
        if self.runtime == "openrouter_batch" and self.judge_enabled:
            raise ValueError("Judge requires the Claude Code runtime, not historical batch mode")
        for role in (
            ("finder", "validator", "judge") if self.judge_enabled else ("finder", "validator")
        ):
            provider = getattr(self, f"{role}_provider")
            model = getattr(self, f"{role}_model")
            if any(char.isspace() for char in model) or model.startswith("-"):
                raise ValueError(f"{role}: use a model ID without whitespace or leading '-' ")
            if self.runtime == "openrouter_batch":
                if provider != "openrouter" or not model.endswith(":batch"):
                    raise ValueError("Historical batch execution requires OpenRouter :batch models")
                continue
            if model.endswith(":batch"):
                raise ValueError(
                    f"{role}: Claude Code needs an interactive model; choose "
                    "deepseek/deepseek-v4.1-flash without :batch for OpenRouter"
                )
            if provider == "openrouter" and model.count("/") != 1:
                raise ValueError(f"{role}: OpenRouter model must use the provider/model format")
            if provider == "anthropic" and "/" in model:
                raise ValueError(f"{role}: Anthropic uses a native Claude model ID, such as sonnet")
        return self

    @field_validator("finder_model", "validator_model", "judge_model")
    @classmethod
    def nonempty_model(cls, value: str) -> str:
        if not value:
            raise ValueError("A role model is required")
        return value

    @field_validator("connectors")
    @classmethod
    def require_search(cls, value: list[str]) -> list[str]:
        if not {"web_search", "duckduckgo", "bing", "search_auto"} & set(value):
            raise ValueError(
                "Enable general web search: web_search, duckduckgo, bing or search_auto"
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


BusinessModelKind = Literal[
    "services", "manufacturing", "goods", "intelligence", "software", "marketplace", "other"
]


class Proposal(Contract):
    """Finder's brief (schema 1.1 adds the required ``business_model_kind``).

    Stored 1.0 proposals are never rewritten; ``termination.proposal_kind``
    reads them as ``unspecified``.
    """

    # Narrows the shared 1.0 literal; only this contract moved to 1.1.
    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    title: str = Field(min_length=1)
    target_customer: str = Field(min_length=1)
    buyer: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    offering: str = Field(min_length=1)
    business_model: str = Field(min_length=1)
    business_model_kind: BusinessModelKind
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


class JudgmentAssessment(Contract):
    economics: str = Field(min_length=1)
    opportunity_cost: str = Field(min_length=1)
    observed_transaction: str = Field(min_length=1)
    competitive_pressure: str = Field(min_length=1)
    objective_fit: str = Field(min_length=1)
    convergence_risk: str = Field(min_length=1)


class Judgment(Contract):
    reviewed_proposal_id: str = Field(min_length=1)
    reviewed_proposal_hash: str = Field(min_length=1)
    reviewed_validation_id: str = Field(min_length=1)
    reviewed_validation_hash: str = Field(min_length=1)
    verdict: Literal["approve", "revise", "reject"]
    worth_pursuing: bool
    summary: str = Field(min_length=1)
    assessment: JudgmentAssessment
    objections: list[Objection] = Field(default_factory=list)
    resolved_objection_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    next_research: list[str] = Field(default_factory=list)
    next_tests: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def approval_shape(self) -> "Judgment":
        if self.verdict == "approve":
            if not self.worth_pursuing:
                raise ValueError("Judge cannot approve a no-go or a candidate not worth pursuing")
            if any(o.severity in {"critical", "major"} and not o.resolved for o in self.objections):
                raise ValueError(
                    "Judge approval cannot leave critical or major objections unresolved"
                )
        elif not self.objections:
            raise ValueError("Judge revision or rejection needs stable objections for Finder")
        if self.verdict != "approve" and self.worth_pursuing:
            raise ValueError("Only an approved opportunity can be marked worth pursuing")
        return self


class VerdictInput(Contract):
    verdict: Literal["good", "weak", "wrong", "untested"]
    notes: str = ""

    @model_validator(mode="after")
    def wrong_needs_notes(self) -> "VerdictInput":
        if self.verdict == "wrong" and not self.notes:
            raise ValueError("A 'wrong' verdict must say what is not viable or factually wrong")
        return self


class QualityMetrics(Contract):
    """Deterministic evidence, independence, process and outcome measures (schema 1.0).

    A metric that cannot be computed is ``None`` with the reason in ``notes``.
    """

    discovery_id: str
    cited_evidence_count: int | None
    cited_origin_count: int | None
    cited_connector_counts: dict[str, int] | None
    snippet_citation_share: float | None
    fact_claims_with_quote_rate: float | None
    claims_with_evidence_rate: float | None
    published_at_known_share: float | None
    median_evidence_age_days: float | None
    validator_query_novelty_min: float | None
    accepting_turn_query_novelty: float | None
    validator_new_origin_share: float | None
    turns_completed: int
    proposal_count: int
    pivot_count: int
    turns_to_first_accept: int | None
    judge_verdict_counts: dict[str, int]
    research_calls: int
    research_failure_share: float | None
    local_denials: int
    known_cost_usd: Decimal
    estimated_cost_usd: Decimal
    unknown_operations: int
    outcome: str
    stop_reason: str
    judge_required: bool
    judge_approved: bool
    human_verdict: str | None
    finder_model: str | None
    validator_model: str | None
    judge_model: str | None
    same_model_family: bool
    prompt_hashes: dict[str, str]
    notes: list[str] = Field(default_factory=list)


class Report(BaseModel):
    model_config = Contract.model_config
    # Stored 1.1 reports are not rewritten; regenerate_report produces 1.2.
    schema_version: Literal["1.1", "1.2"] = "1.2"
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
    judgments: list[dict[str, Any]] = Field(default_factory=list)
    latest_judgment: dict[str, Any] | None = None
    judge_required: bool = False
    judge_approved: bool = False
    quality: QualityMetrics | None = None
    human_verdict: dict[str, Any] | None = None
