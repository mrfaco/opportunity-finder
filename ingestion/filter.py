"""Binary opportunity classifier — Haiku-powered filter.

Given the raw text of an ingested item, decide whether it describes an unmet
user need worth investigating. Output is a structured ``FilterVerdict``.

This module is a workflow step — a single deterministic model call, not an
agentic loop. The classifier prompt lives in ``prompts/filter/classifier.md``
(git-managed); the canonical hash of the prompt used is recorded on every
verdict so eval runs can be keyed by prompt version.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from core.llm import call_cheap_model

# Classification needs little room — a boolean, a float, one or two sentences.
_MAX_TOKENS = 512


class ClassifierResponse(BaseModel):
    """The structured shape the model is constrained to return."""

    is_opportunity: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class FilterVerdict(BaseModel):
    """A classification result plus the metadata needed to persist + audit it."""

    is_opportunity: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    prompt_hash: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int


def _build_user_content(content: str, context: dict | None) -> str:
    """Render the item (and any context) into the user-turn text.

    Context is serialized deterministically so identical items produce
    identical requests — relevant once we add cross-run caching.
    """
    if context:
        context_block = json.dumps(context, sort_keys=True, indent=2)
        return f"Context:\n{context_block}\n\nItem to classify:\n{content}"
    return f"Item to classify:\n{content}"


def classify_content(
    content: str,
    context: dict | None = None,
    prompt: prompt_loader.Prompt | None = None,
) -> FilterVerdict:
    """Classify a single piece of text as an opportunity or not.

    Used by both the ingestion pipeline (on each ingested item) and the eval
    runner (on each labeled eval-set item). Makes one Haiku call with the
    classifier prompt cached as the system prompt.

    ``prompt`` defaults to the current ``prompts/filter/classifier.md``. The
    eval runner passes an explicit prompt to evaluate a specific version.

    Errors from the SDK (auth, rate limit, server) propagate — the caller
    decides how to record the failure. No fallback verdict.
    """
    if prompt is None:
        prompt = prompt_loader.load_prompt("filter", "classifier")

    response = call_cheap_model(
        system_prompt=prompt.content,
        user_content=_build_user_content(content, context),
        output_schema=ClassifierResponse,
        max_tokens=_MAX_TOKENS,
    )
    parsed = response.parsed
    assert isinstance(parsed, ClassifierResponse)
    return FilterVerdict(
        is_opportunity=parsed.is_opportunity,
        confidence=parsed.confidence,
        reason=parsed.reason,
        prompt_hash=prompt.hash,
        model=response.model_used,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cached_tokens=response.cached_tokens,
        latency_ms=response.latency_ms,
    )
