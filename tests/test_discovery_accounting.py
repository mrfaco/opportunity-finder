"""Focused published-rate reconciliation and budget-accounting regressions."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from discovery.accounting import (
    estimate_ollama_usage,
    normalize_claude_usage,
    ollama_pricing,
    reconcile_ollama_discovery,
)
from discovery.models import Discovery, DiscoveryTask, ResearchOperation


def test_cloud_usage_requires_all_observed_counters():
    with pytest.raises(ValueError, match="cache_read_input_tokens"):
        normalize_claude_usage({"input_tokens": 1, "output_tokens": 1})
    assert (
        normalize_claude_usage(
            {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        )["prompt_tokens"]
        == 0
    )


def test_cache_tokens_are_discounted_separately_and_tariff_is_conservative():
    pricing = ollama_pricing("deepseek-v4.1-flash:cloud")
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "prompt_tokens_details": {"cached_tokens": 1_000_000},
    }
    peak = datetime(2026, 11, 3, 14, tzinfo=UTC)
    off_peak = datetime(2026, 11, 3, 20, tzinfo=UTC)
    crossing = datetime(2026, 11, 3, 11, 59, tzinfo=UTC)
    assert Decimal(estimate_ollama_usage(pricing, usage, peak, peak)["cost_usd"]) == Decimal(
        "1.506"
    )
    assert Decimal(
        estimate_ollama_usage(pricing, usage, off_peak, off_peak)["cost_usd"]
    ) == Decimal("0.753")
    assert Decimal(
        estimate_ollama_usage(pricing, usage, crossing, crossing + timedelta(minutes=2))["cost_usd"]
    ) == Decimal("1.506")


@pytest.mark.django_db
def test_reconciliation_is_dry_by_default_and_idempotent_with_unknown_wakes():
    discovery = Discovery.objects.create(
        objective="usage reconciliation", request_hash="test", status="completed"
    )
    task = DiscoveryTask.objects.create(
        discovery=discovery, role="finder", turn_number=1, state="done"
    )
    at = datetime(2026, 11, 3, 14, tzinfo=UTC)
    wake = ResearchOperation.objects.create(
        discovery=discovery,
        task=task,
        operation_key="wake",
        kind="agent_wake",
        state="completed",
        request={"provider": "ollama", "model": "deepseek-v4.1-flash:cloud"},
        response={
            "result": {"total_cost_usd": 99},
            "usage": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 1_000_000,
                "prompt_tokens_details": {"cached_tokens": 1_000_000},
            },
        },
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=1_000_000,
        completed_at=at + timedelta(seconds=50),
    )
    ResearchOperation.objects.filter(pk=wake.pk).update(created_at=at)
    ResearchOperation.objects.create(
        discovery=discovery,
        task=task,
        operation_key="unsettled-wake",
        kind="agent_wake",
        state="unknown",
        request={"provider": "ollama", "model": "deepseek-v4.1-flash:cloud"},
    )
    missing = ResearchOperation.objects.create(
        discovery=discovery,
        task=task,
        operation_key="missing-usage",
        kind="agent_wake",
        state="completed",
        request={"provider": "ollama", "model": "deepseek-v4.1-flash:cloud"},
        completed_at=at + timedelta(seconds=50),
    )
    out = StringIO()
    call_command("reconcile_discovery_costs", discovery=[str(discovery.pk)], stdout=out)
    dry = json.loads(out.getvalue())
    assert not dry["applied"]
    assert dry["usage_incomplete"]
    assert dry["missing_usage_wakes"] == [str(missing.pk)]
    assert Decimal(dry["reconciled_recorded_cost_usd"]) == Decimal("1.506")
    wake.refresh_from_db()
    assert wake.cost_usd == 0
    first = reconcile_ollama_discovery(str(discovery.pk), apply=True)
    second = reconcile_ollama_discovery(str(discovery.pk), apply=True)
    assert first["reconciled_recorded_cost_usd"] == second["reconciled_recorded_cost_usd"]
    discovery.refresh_from_db()
    wake.refresh_from_db()
    assert discovery.cost_used_usd == Decimal("1.506")
    assert wake.response["result"]["total_cost_usd"] == 99
    assert wake.response["cost_is_estimate"]
    assert wake.response["cloud_charges_unverified"]
    assert wake.response["pre_reconciliation_cost_usd"] == "0E-8"
    missing.refresh_from_db()
    assert missing.response["cost_unknown"]
