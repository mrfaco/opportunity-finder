"""Cheap-tier LLM dispatcher — auto-selects provider by configured key.

Three modules need a single structured cheap-model call:
* ``ingestion/filter.py`` — opportunity classifier
* ``clusters/summarizer.py`` — cluster title + summary
* ``clusters/judges.py`` — merge judge

This module wraps both supported providers behind one
``call_cheap_model()`` function. Selection rules:

1. ``settings.CHEAP_LLM_PROVIDER == "anthropic"`` → Anthropic
   (``MODEL_FILTER``, default ``claude-haiku-4-5``).
2. ``settings.CHEAP_LLM_PROVIDER == "openrouter"`` → OpenRouter
   (``OPENROUTER_MODEL_FILTER``, default ``deepseek/deepseek-chat``).
3. ``settings.CHEAP_LLM_PROVIDER == "auto"`` (default) — pick by which
   key is set; Anthropic wins when both are set, since the agent loop
   (``agents/loop.py``) also requires Anthropic and consistency between
   tiers avoids "cheap tier works but loop doesn't" footguns.
4. Provider chosen but the corresponding key is missing → raise.
5. Neither key set in auto mode → raise.

Realistic "cut the cheap-tier bill" config: keep ``ANTHROPIC_API_KEY``
set (so the agent loop works), set ``OPENROUTER_API_KEY``, and set
``CHEAP_LLM_PROVIDER=openrouter`` to explicitly route this tier there.
"""

from __future__ import annotations

import json
import time
from typing import TypeVar

from django.conf import settings
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class CheapModelResponse(BaseModel):
    """Provider-agnostic result of a structured cheap-model call."""

    # The parsed instance is ``Any`` from this module's perspective; the
    # caller's type checker treats it as the concrete output schema.
    # Pydantic can't store an arbitrary BaseModel in a field reliably, so
    # we use a typed dict + the caller reconstructs the model on the
    # other side. Actually simpler — declare ``parsed`` as object via
    # ``model_config = ConfigDict(arbitrary_types_allowed=True)``.

    model_config = {"arbitrary_types_allowed": True}

    parsed: BaseModel
    model_used: str
    provider: str  # "anthropic" or "openrouter"
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int


def call_cheap_model(
    *,
    system_prompt: str,
    user_content: str,
    output_schema: type[T],
    max_tokens: int = 512,
) -> CheapModelResponse:
    """One structured call to the cheap-tier LLM.

    Args:
        system_prompt: full text of the system/instructions prompt.
        user_content: the user-turn payload.
        output_schema: a Pydantic model class; the response is validated
            against it and the parsed instance is returned in the
            response's ``parsed`` field.
        max_tokens: generation cap. Cheap-tier tasks are typically tight
            (a verdict + short reasoning); 512 is usually plenty.

    Returns:
        ``CheapModelResponse`` with the validated instance + usage stats.

    Raises:
        RuntimeError: no provider key set, or the chosen provider failed
            to return a parseable response.
        pydantic.ValidationError: the provider returned JSON that doesn't
            match the schema. Surfaced to the caller verbatim — the
            ``Tool.dispatch`` translation layer already understands this
            for agent-loop callers; the cheap-tier callers just let it
            propagate.
    """
    provider = _select_provider()
    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_content, output_schema, max_tokens)
    if provider == "openrouter":
        return _call_openrouter(system_prompt, user_content, output_schema, max_tokens)
    # ``_select_provider`` raises on bad config; this is unreachable.
    raise AssertionError(f"unreachable: provider={provider!r}")


def _select_provider() -> str:
    """Resolve ``settings.CHEAP_LLM_PROVIDER`` against the available keys.

    Returns ``"anthropic"`` or ``"openrouter"``. Raises ``RuntimeError``
    on misconfiguration (unknown value, key not set for chosen provider,
    or neither key set in auto mode).
    """
    choice = (settings.CHEAP_LLM_PROVIDER or "auto").lower()
    if choice == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("CHEAP_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        return "anthropic"
    if choice == "openrouter":
        if not settings.OPENROUTER_API_KEY:
            raise RuntimeError("CHEAP_LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set.")
        return "openrouter"
    if choice != "auto":
        raise RuntimeError(
            f"CHEAP_LLM_PROVIDER={choice!r} is not recognized. "
            "Allowed values: auto, anthropic, openrouter."
        )
    # ``auto``: prefer Anthropic when set (matches the agent loop), else
    # fall back to OpenRouter, else fail.
    if settings.ANTHROPIC_API_KEY:
        return "anthropic"
    if settings.OPENROUTER_API_KEY:
        return "openrouter"
    raise RuntimeError(
        "No LLM provider configured. Set ANTHROPIC_API_KEY or "
        "OPENROUTER_API_KEY in .env (or set CHEAP_LLM_PROVIDER explicitly)."
    )


def _call_anthropic(
    system_prompt: str,
    user_content: str,
    output_schema: type[T],
    max_tokens: int,
) -> CheapModelResponse:
    """Anthropic path — uses ``messages.parse()`` for native structured output.

    Mirrors the previous shape from ``ingestion/filter.py`` (which is
    where this pattern originated) including the ``cache_control``
    ephemeral breakpoint on the system prompt. The breakpoint is a no-op
    below Anthropic's minimum-cacheable-prefix length, harmless above.
    """
    import anthropic  # noqa: PLC0415  # deferred — provider-specific dep

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    model = settings.MODEL_FILTER

    started = time.monotonic()
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=output_schema,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Anthropic returned no parsed output (stop_reason="
            f"{response.stop_reason!r}, model={model})."
        )

    usage = response.usage
    return CheapModelResponse(
        parsed=parsed,
        model_used=model,
        provider="anthropic",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        latency_ms=latency_ms,
    )


def _call_openrouter(
    system_prompt: str,
    user_content: str,
    output_schema: type[T],
    max_tokens: int,
) -> CheapModelResponse:
    """OpenRouter path — OpenAI-compatible API + JSON mode.

    OpenRouter is a proxy. Schema enforcement varies by underlying
    provider, so we use JSON mode (``response_format={"type":
    "json_object"}``) and validate client-side against the Pydantic
    schema. The schema is also rendered into the system prompt so the
    model knows the exact shape to emit — DeepSeek-V3 follows this
    reliably in practice; if you swap to a less-aligned model you may
    need to retry on validation failure.
    """
    import openai  # noqa: PLC0415  # deferred — provider-specific dep

    client = openai.OpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )
    model = settings.OPENROUTER_MODEL_FILTER

    # Render the schema into the prompt. DeepSeek-V3 et al. lean on this
    # signal more reliably than on the response_format hint alone.
    schema_dict = output_schema.model_json_schema()
    augmented_system = (
        f"{system_prompt}\n\n"
        f"Respond with a single JSON object that conforms to this schema:\n"
        f"{json.dumps(schema_dict, indent=2)}"
    )

    started = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": augmented_system},
            {"role": "user", "content": user_content},
        ],
        # OpenRouter-specific headers help with their analytics; harmless
        # if unset. Skip unless you have a project URL to attribute.
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    if not response.choices or not response.choices[0].message.content:
        raise RuntimeError(
            f"OpenRouter returned no content (model={model}, "
            f"finish_reason={response.choices[0].finish_reason if response.choices else None!r})."
        )

    raw = response.choices[0].message.content
    try:
        parsed = output_schema.model_validate_json(raw)
    except ValidationError as exc:
        raise RuntimeError(
            f"OpenRouter response did not match schema {output_schema.__name__}: {exc}. "
            f"Raw: {raw[:500]!r}"
        ) from exc

    usage = response.usage
    if usage is None:
        raise RuntimeError(
            f"OpenRouter omitted the usage block (model={model}); cannot record cost."
        )
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0

    return CheapModelResponse(
        parsed=parsed,
        model_used=model,
        provider="openrouter",
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=cached,
        latency_ms=latency_ms,
    )
