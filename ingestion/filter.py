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
import time

from django.conf import settings
from pydantic import BaseModel, Field

from agents import prompts as prompt_loader
from core.anthropic_client import get_client

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
    client = get_client()
    model = settings.MODEL_FILTER

    started = time.monotonic()
    response = client.messages.parse(
        model=model,
        max_tokens=_MAX_TOKENS,
        # System prompt is stable across every call — cache it. (Caching only
        # kicks in once the prompt exceeds the model's minimum cacheable
        # prefix; the breakpoint is harmless below that.)
        system=[
            {
                "type": "text",
                "text": prompt.content,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _build_user_content(content, context)}],
        output_format=ClassifierResponse,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    parsed = response.parsed_output
    if parsed is None:
        # Structured output is enforced server-side; a None here means the
        # model refused or the response was truncated. Fail loud.
        raise RuntimeError(
            f"Classifier returned no parsed output (stop_reason="
            f"{response.stop_reason!r}). Item left unclassified."
        )

    usage = response.usage
    return FilterVerdict(
        is_opportunity=parsed.is_opportunity,
        confidence=parsed.confidence,
        reason=parsed.reason,
        prompt_hash=prompt.hash,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        latency_ms=latency_ms,
    )
