"""Published-rate estimates, separate from provider-verified cloud charges."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum

from discovery.models import Discovery, ResearchOperation

PRICING_SOURCE = "https://ollama.com/pricing"
PRICING_CHECKED = "2026-11-04"
# USD per million tokens: input, cached input, output. Rates are an explicit
# checked snapshot, never Claude Code's unrelated model valuation.
OLLAMA_RATES = {
    "deepseek-v4.1-flash": ("0.30", "0.006", "1.20"),
    "deepseek-v4-pro": ("1.32", "0.044", "3.96"),
    "glm-5.3": ("1.40", "0.26", "4.40"),
    "glm-5.3-flash": ("0.15", "0.03", "0.50"),
    "glm-5.2": ("1.40", "0.26", "4.40"),
    "kimi-k3": ("3.00", "0.30", "15.00"),
    "kimi-k2.7-code": ("0.95", "0.19", "4.00"),
    "kimi-k2.6": ("0.95", "0.16", "4.00"),
    "minimax-m3.1-flash": ("0.15", "0.015", "0.60"),
    "minimax-m3": ("0.60", "0.12", "2.40"),
    "minimax-m2.7": ("0.30", "0.06", "1.20"),
}


def normalize_claude_usage(usage: dict[str, Any]) -> dict[str, Any]:
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Claude Code omitted or invalidated cloud token counter {key}")
    created = usage.get("cache_creation_input_tokens", 0)
    if isinstance(created, bool) or not isinstance(created, int) or created < 0:
        raise ValueError("Claude Code returned invalid cache creation token accounting")
    return {
        "prompt_tokens": usage["input_tokens"] + created,
        "completion_tokens": usage["output_tokens"],
        "prompt_tokens_details": {"cached_tokens": usage["cache_read_input_tokens"]},
    }


def ollama_pricing(model: str) -> dict[str, Any]:
    name = model.removesuffix(":cloud")
    if name not in OLLAMA_RATES:
        raise ValueError(f"No checked Ollama Cloud pricing for {model}; add published rates first")
    input_rate, cache_rate, output_rate = OLLAMA_RATES[name]
    return {
        "schema_version": "1.0",
        "model": model,
        "source": PRICING_SOURCE,
        "checked_on": PRICING_CHECKED,
        "input_per_million": input_rate,
        "cached_input_per_million": cache_rate,
        "output_per_million": output_rate,
        "off_peak_multiplier": "0.5" if name.startswith("deepseek-") else "1",
        "off_peak_rule": "Outside 12:00–18:00 UTC weekdays; all weekend",
    }


def _entirely_off_peak(started_at: datetime, completed_at: datetime) -> bool:
    start, end = started_at.astimezone(UTC), completed_at.astimezone(UTC)
    day = start.date()
    while day <= end.date():
        if day.weekday() < 5:
            peak_start = datetime.combine(day, time(12), tzinfo=UTC)
            peak_end = datetime.combine(day, time(18), tzinfo=UTC)
            if start < peak_end and end > peak_start:
                return False
        day += timedelta(days=1)
    return True


def estimate_ollama_usage(
    pricing: dict[str, Any],
    usage: dict[str, Any],
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    tokens = {
        "input": int(usage.get("prompt_tokens", 0)),
        "cached_input": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
        "output": int(usage.get("completion_tokens", 0)),
    }
    if any(value < 0 for value in tokens.values()):
        raise ValueError("Token counts must be non-negative")
    off_peak = _entirely_off_peak(started_at, completed_at)
    multiplier = Decimal(pricing["off_peak_multiplier"]) if off_peak else Decimal("1")
    amount = (
        sum(Decimal(tokens[key]) * Decimal(pricing[f"{key}_per_million"]) for key in tokens)
        * multiplier
        / Decimal("1000000")
    )
    return {
        "schema_version": "1.1",
        "cost_usd": str(amount.quantize(Decimal("0.00000001"))),
        "cost_is_estimate": True,
        "cost_unknown": False,
        "cloud_charges_unverified": True,
        "budget_enforcement": "between_wake_estimate",
        "pricing_basis": {
            **pricing,
            "tariff": "off_peak" if off_peak else "peak_upper_estimate",
            "multiplier": str(multiplier),
            "wake_started_at": started_at.isoformat(),
            "wake_completed_at": completed_at.isoformat(),
            "note": "Aggregate wake tokens lack individual request timestamps. "
            "A wake overlapping peak time uses peak rates for all tokens. "
            "This values observed tokens; it is not an account billing receipt.",
        },
    }


def reconcile_ollama_discovery(discovery_id: str, *, apply: bool = False) -> dict[str, Any]:
    """Idempotently revalue recorded completed wakes, without calling providers."""
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=discovery_id)
        if apply and discovery.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Only terminal discoveries can be reconciled with --apply")
        rows = []
        missing_usage = []
        unsettled = list(
            discovery.operations.filter(kind="agent_wake", state__in=["started", "unknown"]).values(
                "id", "state", "request"
            )
        )
        delta = Decimal("0")
        for operation in discovery.operations.filter(kind="agent_wake", state="completed"):
            if operation.request.get("provider") != "ollama":
                continue
            model = operation.request.get("model", "")
            if not model.endswith(":cloud"):
                continue
            if operation.completed_at is None:
                raise ValueError(f"Completed operation {operation.pk} has no completion timestamp")
            # Frozen runtime profiles remain unchanged. Reconciliation records its
            # own dated pricing basis alongside the original reported valuation.
            pricing = operation.response.get("pricing_basis") or ollama_pricing(model)
            recorded_usage = operation.response.get("usage") or {}
            has_usage = (
                "prompt_tokens" in recorded_usage
                and "completion_tokens" in recorded_usage
                and "cached_tokens" in (recorded_usage.get("prompt_tokens_details") or {})
            )
            if not has_usage and not any(
                (operation.input_tokens, operation.output_tokens, operation.cached_tokens)
            ):
                missing_usage.append(str(operation.pk))
                if apply:
                    ResearchOperation.objects.filter(pk=operation.pk).update(
                        response={
                            **operation.response,
                            "cost_unknown": True,
                            "cloud_charges_unverified": True,
                            "accounting_note": "Completed wake omitted observed token usage; "
                            "recorded cost is incomplete.",
                        }
                    )
                continue
            usage = (
                recorded_usage
                if has_usage
                else {
                    "prompt_tokens": operation.input_tokens,
                    "completion_tokens": operation.output_tokens,
                    "prompt_tokens_details": {"cached_tokens": operation.cached_tokens},
                }
            )
            estimate = estimate_ollama_usage(
                pricing, usage, operation.created_at, operation.completed_at
            )
            amount = Decimal(estimate["cost_usd"])
            delta += amount - operation.cost_usd
            rows.append({"operation_id": str(operation.pk), **estimate})
            if apply:
                response = {**operation.response, **estimate}
                response.setdefault("pre_reconciliation_cost_usd", str(operation.cost_usd))
                response["accounting_note"] = (
                    "Published-rate token estimate; actual Ollama account charges unverified. "
                    "Claude Code total_cost_usd is retained as unrelated reported valuation."
                )
                ResearchOperation.objects.filter(pk=operation.pk).update(
                    response=response, cost_usd=amount
                )
        before = discovery.cost_used_usd
        expected = discovery.operations.filter(state="completed").aggregate(total=Sum("cost_usd"))[
            "total"
        ] or Decimal("0")
        after = expected if apply else expected + delta
        if apply:
            Discovery.objects.filter(pk=discovery.pk).update(cost_used_usd=after)
        return {
            "discovery_id": str(discovery.pk),
            "applied": apply,
            "previous_recorded_cost_usd": str(before),
            "reconciled_recorded_cost_usd": str(after),
            "cloud_wakes": len(rows),
            "unsettled_wakes": [
                {"operation_id": str(row["id"]), "state": row["state"], "request": row["request"]}
                for row in unsettled
            ],
            "missing_usage_wakes": missing_usage,
            "usage_incomplete": bool(unsettled or missing_usage),
            "operations": rows,
            "billing_verified": False,
            "scope": "This discovery only; original costs in predecessor runs stay there.",
        }
