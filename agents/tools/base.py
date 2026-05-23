"""MCP-compatible tool primitives.

A ``Tool`` carries a ``name``, ``description``, JSON-Schema input/output, plus
internal metadata for the orchestrator (cost tier, cache TTL). Exposing these
as an MCP server in v2 is a matter of plumbing — the shape already matches.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError


class ToolInput(BaseModel):
    """Base class for tool inputs. Use ``.model_json_schema()`` for JSON Schema."""

    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    """All tool outputs include status. Concrete tools subclass this."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error", "rate_limited", "timeout", "not_found", "validation_failed"]
    error_reason: str | None = None
    retry_after_seconds: int | None = None


class ToolDefinition(BaseModel):
    """Snapshot of a tool — what the orchestrator persists, what MCP exports."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    cost_tier: int  # 0=internal, 1=free external, 2=paid external, 3=expensive
    cache_ttl_seconds: int | None
    cache_errors: dict[str, int]


# Tool inputs are bound to ``BaseModel``, not ``ToolInput``, so existing
# Pydantic schemas (e.g. ``investigations.schemas.Brief``) can be used as
# tool input types directly without forcing them through a wrapper.
InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=ToolOutput)


class Tool(Generic[InT, OutT]):
    def __init__(
        self,
        name: str,
        description: str,
        input_type: type[InT],
        output_type: type[OutT],
        impl: Callable[[InT], OutT],
        cost_tier: int,
        cache_ttl_seconds: int | None = 0,
        cache_errors: dict[str, int] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_type = input_type
        self.output_type = output_type
        self.impl = impl
        self.cost_tier = cost_tier
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_errors = cache_errors or {}

    def input_schema(self) -> dict[str, Any]:
        return self.input_type.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self.output_type.model_json_schema()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema(),
            output_schema=self.output_schema(),
            cost_tier=self.cost_tier,
            cache_ttl_seconds=self.cache_ttl_seconds,
            cache_errors=self.cache_errors,
        )

    def dispatch(self, input_data: dict[str, Any]) -> OutT:
        """Validate input → call impl → return the implementation's output.

        Pydantic validation failures become ``status='validation_failed'``
        outputs by design — this is the tool's *contract* with the agent, not
        a fallback. The agent uses the structured failure to self-correct
        its next call. Other exceptions raised inside ``impl`` propagate.
        """
        try:
            parsed_input = self.input_type.model_validate(input_data)
        except ValidationError as exc:  # allow: suppress-exception
            return self.output_type(status="validation_failed", error_reason=str(exc))
        return self.impl(parsed_input)
