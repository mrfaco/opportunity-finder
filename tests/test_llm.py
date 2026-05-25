"""Tests for the cheap-tier LLM dispatcher (core/llm.py).

Covers:
* Provider selection by configured key.
* Anthropic precedence when both keys are set.
* Loud-fail when neither key is set.
* The Anthropic path's call shape (cache_control on the system prompt,
  output_format=schema, correct usage parsing).
* The OpenRouter path's call shape (JSON mode, schema appended to the
  system prompt, schema validation on the response).
* Loud-fail on schema mismatch from OpenRouter (no silent fallback).

The Anthropic + OpenAI SDKs are mocked at module level so these tests
never touch the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from core.llm import CheapModelResponse, call_cheap_model


class _StubSchema(BaseModel):
    """Tiny schema used to verify dispatcher behavior."""

    verdict: bool
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Selection precedence
# ---------------------------------------------------------------------------


def test_raises_when_no_provider_configured(settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = ""
    settings.CHEAP_LLM_PROVIDER = "auto"
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        call_cheap_model(system_prompt="hi", user_content="hello", output_schema=_StubSchema)


def test_anthropic_wins_when_both_keys_set(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = "or-key"
    settings.CHEAP_LLM_PROVIDER = "auto"
    called = {"provider": None}

    def _stub_anthropic(*_a, **_kw):
        called["provider"] = "anthropic"
        return CheapModelResponse(
            parsed=_StubSchema(verdict=True, confidence=0.9),
            model_used="claude-haiku-4-5",
            provider="anthropic",
            input_tokens=10,
            output_tokens=2,
            latency_ms=1,
        )

    def _stub_openrouter(*_a, **_kw):
        called["provider"] = "openrouter"
        return CheapModelResponse(
            parsed=_StubSchema(verdict=False, confidence=0.5),
            model_used="deepseek/deepseek-chat",
            provider="openrouter",
            input_tokens=10,
            output_tokens=2,
            latency_ms=1,
        )

    monkeypatch.setattr("core.llm._call_anthropic", _stub_anthropic)
    monkeypatch.setattr("core.llm._call_openrouter", _stub_openrouter)
    result = call_cheap_model(system_prompt="hi", user_content="hello", output_schema=_StubSchema)
    assert called["provider"] == "anthropic"
    assert result.provider == "anthropic"


def test_openrouter_used_when_only_openrouter_configured(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-key"
    settings.CHEAP_LLM_PROVIDER = "auto"

    def _stub_openrouter(*_a, **_kw):
        return CheapModelResponse(
            parsed=_StubSchema(verdict=True, confidence=0.7),
            model_used="deepseek/deepseek-chat",
            provider="openrouter",
            input_tokens=10,
            output_tokens=2,
            latency_ms=1,
        )

    monkeypatch.setattr("core.llm._call_openrouter", _stub_openrouter)
    result = call_cheap_model(system_prompt="hi", user_content="hello", output_schema=_StubSchema)
    assert result.provider == "openrouter"


def test_explicit_openrouter_override_wins_over_auto(monkeypatch, settings):
    """The realistic ``cut costs without breaking the loop`` config: both
    keys set, but ``CHEAP_LLM_PROVIDER=openrouter`` forces this tier to
    route through OpenRouter while the agent loop keeps using Anthropic."""
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = "or-key"
    settings.CHEAP_LLM_PROVIDER = "openrouter"

    called = {"provider": None}

    def _stub_openrouter(*_a, **_kw):
        called["provider"] = "openrouter"
        return CheapModelResponse(
            parsed=_StubSchema(verdict=True, confidence=0.6),
            model_used="deepseek/deepseek-chat",
            provider="openrouter",
            input_tokens=10,
            output_tokens=2,
            latency_ms=1,
        )

    def _stub_anthropic(*_a, **_kw):
        called["provider"] = "anthropic"
        raise AssertionError("anthropic path should not be entered")

    monkeypatch.setattr("core.llm._call_openrouter", _stub_openrouter)
    monkeypatch.setattr("core.llm._call_anthropic", _stub_anthropic)
    call_cheap_model(system_prompt="hi", user_content="hello", output_schema=_StubSchema)
    assert called["provider"] == "openrouter"


def test_explicit_anthropic_override_raises_if_key_missing(settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-key"
    settings.CHEAP_LLM_PROVIDER = "anthropic"
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)


def test_explicit_openrouter_override_raises_if_key_missing(settings):
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = ""
    settings.CHEAP_LLM_PROVIDER = "openrouter"
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)


def test_unknown_provider_choice_raises(settings):
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = "or-key"
    settings.CHEAP_LLM_PROVIDER = "deepmind"
    with pytest.raises(RuntimeError, match="not recognized"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)


# ---------------------------------------------------------------------------
# Anthropic path
# ---------------------------------------------------------------------------


def test_anthropic_path_sends_cache_control_and_parses_usage(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = ""
    settings.MODEL_FILTER = "claude-haiku-4-5"

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = SimpleNamespace(
        parsed_output=_StubSchema(verdict=True, confidence=0.81),
        usage=SimpleNamespace(input_tokens=80, output_tokens=12, cache_read_input_tokens=4),
        stop_reason="end_turn",
    )

    monkeypatch.setattr("anthropic.Anthropic", lambda **_kw: fake_client)
    result = call_cheap_model(
        system_prompt="be helpful",
        user_content="classify this",
        output_schema=_StubSchema,
    )
    assert result.provider == "anthropic"
    assert result.parsed.verdict is True
    assert result.input_tokens == 80
    assert result.cached_tokens == 4
    # The cache_control breakpoint must be on the system message — it's
    # the load-bearing optimization for our use case.
    _, kwargs = fake_client.messages.parse.call_args
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["output_format"] is _StubSchema


def test_anthropic_path_raises_on_no_parsed_output(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "ant-key"
    settings.OPENROUTER_API_KEY = ""

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = SimpleNamespace(
        parsed_output=None,
        usage=SimpleNamespace(input_tokens=5, output_tokens=0, cache_read_input_tokens=0),
        stop_reason="refusal",
    )
    monkeypatch.setattr("anthropic.Anthropic", lambda **_kw: fake_client)
    with pytest.raises(RuntimeError, match="no parsed output"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)


# ---------------------------------------------------------------------------
# OpenRouter path
# ---------------------------------------------------------------------------


def test_openrouter_path_uses_json_mode_and_validates_response(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-key"
    settings.OPENROUTER_MODEL_FILTER = "deepseek/deepseek-chat"

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"verdict": true, "confidence": 0.71}'),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=18,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )
    monkeypatch.setattr("openai.OpenAI", lambda **_kw: fake_client)

    result = call_cheap_model(
        system_prompt="classifier prompt",
        user_content="item to classify",
        output_schema=_StubSchema,
    )
    assert result.provider == "openrouter"
    assert result.parsed.verdict is True
    assert result.parsed.confidence == 0.71
    assert result.input_tokens == 120
    assert result.output_tokens == 18

    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["response_format"] == {"type": "json_object"}
    # Schema is rendered into the system prompt so the model knows the
    # exact shape to emit (DeepSeek leans on this).
    system_message = kwargs["messages"][0]["content"]
    assert "classifier prompt" in system_message
    assert '"verdict"' in system_message  # schema embedded
    assert '"confidence"' in system_message


def test_openrouter_path_raises_on_schema_mismatch(monkeypatch, settings):
    """Loud-fail: when the model emits JSON that doesn't match the schema,
    we raise rather than silently returning a partial result."""
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-key"

    fake_client = MagicMock()
    # Missing the required ``confidence`` field.
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"verdict": true}'),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None),
    )
    monkeypatch.setattr("openai.OpenAI", lambda **_kw: fake_client)
    with pytest.raises(RuntimeError, match="did not match schema"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)


def test_openrouter_path_raises_on_empty_content(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-key"

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=0, prompt_tokens_details=None),
    )
    monkeypatch.setattr("openai.OpenAI", lambda **_kw: fake_client)
    with pytest.raises(RuntimeError, match="no content"):
        call_cheap_model(system_prompt="x", user_content="y", output_schema=_StubSchema)
