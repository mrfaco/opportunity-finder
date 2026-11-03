"""OpenRouter batch transport; each dependent agent step is its own durable batch."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings

from discovery.errors import BatchPending, DiscoveryError, OutcomeUnknown
from discovery.models import ResearchOperation
from discovery.schemas import DiscoveryConfig

BASE_URL = "https://openrouter.ai/api/v1"


def check_configuration(config: DiscoveryConfig) -> dict[str, Any]:
    if config.runtime == "claude_code":
        from discovery.claude_runtime import check_configuration as check_claude  # noqa: PLC0415

        return check_claude(config)
    if not settings.OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is required for discovery")
    provider = settings.DISCOVERY_BATCH_PROVIDER
    models = {}
    for model in sorted({config.finder_model, config.validator_model}):
        if not model.endswith(":batch") or model.count("/") != 1:
            raise ValueError("Discovery models must be OpenRouter model IDs ending in :batch")
        response = httpx.get(f"{BASE_URL}/models/{model}/endpoints", timeout=20)
        response.raise_for_status()
        data = response.json()["data"]
        endpoints = [
            endpoint for endpoint in data["endpoints"] if endpoint["tag"].split("/")[0] == provider
        ]
        if not endpoints:
            raise ValueError(f"No batch endpoint for {model} on configured provider {provider}")
        if not all("tools" in e["supported_parameters"] for e in endpoints):
            raise ValueError(f"{model} does not support agent tools on {provider}")
        # Reserve at the highest published rate for the pinned provider, without
        # assuming cache hits or time-of-day discounts.
        models[model] = {
            "prompt_rate": str(max(Decimal(e["pricing"]["prompt"]) for e in endpoints)),
            "completion_rate": str(max(Decimal(e["pricing"]["completion"]) for e in endpoints)),
            "context_length": min(e.get("context_length") or 1_000_000 for e in endpoints),
        }
    return {"schema_version": "1.0", "provider": provider, "models": models}


def request_body(
    model: str, history: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int
) -> dict[str, Any]:
    return {
        "model": model.removesuffix(":batch"),
        "messages": history,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "reasoning": {"effort": "low"},
    }


def reservation(body: dict[str, Any], snapshot: dict[str, Any], model: str) -> Decimal:
    rates = snapshot["models"][model]
    # UTF-8 bytes conservatively bound text tokens. Include tool schemas and
    # message framing; reject oversized requests before submitting a paid job.
    input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 8192
    if input_bound + body["max_tokens"] > rates["context_length"]:
        raise DiscoveryError("Discovery context exceeds the frozen model context bound")
    return (
        Decimal(input_bound) * Decimal(rates["prompt_rate"])
        + Decimal(body["max_tokens"]) * Decimal(rates["completion_rate"])
    ).quantize(Decimal("0.00000001"))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "Opportunity Finder",
    }


def advance(operation: ResearchOperation, provider: str) -> dict[str, Any]:
    """Submit once, or poll the saved ID. Never sleep or retry a billed POST."""
    if operation.state == "completed":
        return operation.response
    if operation.state in {"started", "unknown"}:
        raise OutcomeUnknown("Model submission outcome unknown; operator reconciliation required")
    if operation.state == "planned":
        operation.state = "started"
        operation.save(update_fields=["state"])
        payload = {
            "endpoint": "/v1/chat/completions",
            "model": operation.request["body"]["model"],
            "provider": {"only": [provider], "allow_fallbacks": False},
            "completion_window": "24h",
            "requests": [{"custom_id": str(operation.id), "body": operation.request["body"]}],
        }
        try:
            response = httpx.post(
                f"{BASE_URL}/batches", headers=_headers(), json=payload, timeout=30
            )
        except httpx.TransportError as exc:
            operation.state = "unknown"
            operation.error_summary = "OpenRouter submission transport failed; outcome unknown"
            operation.save(update_fields=["state", "error_summary"])
            raise OutcomeUnknown(operation.error_summary) from exc
        if not response.is_success:
            # A server failure can occur after submission was accepted upstream.
            operation.state = "unknown" if response.status_code >= 500 else "failed"
            operation.error_summary = (
                f"OpenRouter batch submit returned HTTP {response.status_code}"
            )
            operation.save(update_fields=["state", "error_summary"])
            if operation.state == "unknown":
                raise OutcomeUnknown(operation.error_summary)
            raise DiscoveryError(operation.error_summary)
        data = response.json()
        if not data.get("id"):
            operation.state = "unknown"
            operation.save(update_fields=["state"])
            raise OutcomeUnknown("OpenRouter accepted a batch but omitted its ID")
        operation.response = {"batch_id": data["id"], "provider_status": data.get("status")}
        operation.state = "pending"
        operation.save(update_fields=["response", "state"])
        raise BatchPending("OpenRouter batch submitted")

    response = httpx.get(
        f"{BASE_URL}/batches/{operation.response['batch_id']}", headers=_headers(), timeout=30
    )
    if response.status_code == 429 or response.status_code >= 500:
        raise BatchPending(f"OpenRouter poll temporarily unavailable (HTTP {response.status_code})")
    if not response.is_success:
        raise DiscoveryError(f"OpenRouter batch poll returned HTTP {response.status_code}")
    data = response.json()
    status = data["status"]
    operation.response["provider_status"] = status
    operation.save(update_fields=["response"])
    if status in {"failed", "expired", "cancelled"}:
        raise DiscoveryError(f"OpenRouter batch ended as {status}; inspect provider batch ID")
    if status != "completed" or data.get("results") is None:
        raise BatchPending("OpenRouter batch is still processing")
    results = [r for r in data["results"] if r["custom_id"] == str(operation.id)]
    if len(results) != 1:
        raise DiscoveryError("OpenRouter batch result did not match its operation ID")
    result = results[0]
    if result.get("error") or not result.get("response"):
        raise DiscoveryError("OpenRouter batch request failed; inspect provider batch ID")
    if result["response"].get("status_code", 200) != 200:
        raise DiscoveryError("OpenRouter batch request returned an unsuccessful status")
    completion = result["response"]["body"]
    usage = completion.get("usage") or {}
    batch_usage = data.get("usage") or {}
    actual_cost = batch_usage.get("cost", usage.get("cost"))
    if actual_cost is None:
        raise OutcomeUnknown("OpenRouter completed the batch but omitted cost accounting")
    if not completion.get("choices"):
        raise DiscoveryError("OpenRouter returned no completion choices")
    return {
        "batch_id": operation.response["batch_id"],
        "provider_status": status,
        "message": completion["choices"][0]["message"],
        "usage": usage,
        "cost_usd": str(actual_cost),
        "generation_id": completion.get("id"),
    }
