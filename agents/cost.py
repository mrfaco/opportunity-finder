"""Cost computation + aggregation.

Pricing is in USD per million tokens. Numbers below are placeholders — verify
against the Anthropic pricing page before relying on them.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

# TODO(v1-followup): verify each rate against the Anthropic pricing page
# before treating these numbers as anything beyond an order-of-magnitude
# estimate.
PRICING: dict[tuple[str, str], Decimal] = {
    ("claude-haiku-4-5", "input"): Decimal("1.00"),
    ("claude-haiku-4-5", "output"): Decimal("5.00"),
    ("claude-haiku-4-5", "cached_input"): Decimal("0.10"),
    ("claude-sonnet-4-6", "input"): Decimal("3.00"),
    ("claude-sonnet-4-6", "output"): Decimal("15.00"),
    ("claude-sonnet-4-6", "cached_input"): Decimal("0.30"),
    ("claude-opus-4-7", "input"): Decimal("15.00"),
    ("claude-opus-4-7", "output"): Decimal("75.00"),
    ("claude-opus-4-7", "cached_input"): Decimal("1.50"),
}


def _rate(model: str, kind: str) -> Decimal:
    rate = PRICING.get((model, kind))
    if rate is None:
        # Unknown model — return zero rather than crashing the run.
        return Decimal("0")
    return rate


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> Decimal:
    """USD cost for one model call."""
    million = Decimal("1000000")
    cost = (
        _rate(model, "input") * (Decimal(input_tokens) / million)
        + _rate(model, "output") * (Decimal(output_tokens) / million)
        + _rate(model, "cached_input") * (Decimal(cached_tokens) / million)
    )
    return cost.quantize(Decimal("0.000001"))


def daily_cost_summary(day: date | None = None) -> dict:
    """Per-day aggregate spend, broken down by source.

    Includes both classifier rows (``FilterClassification``) and agent runs
    (``AgentRun``).
    """
    from agents.models import AgentRun
    from ingestion.models import FilterClassification

    target = day or date.today()
    start = target
    end = target + timedelta(days=1)

    filter_qs = FilterClassification.objects.filter(classified_at__gte=start, classified_at__lt=end)
    runs_qs = AgentRun.objects.filter(started_at__gte=start, started_at__lt=end)

    filter_cost = filter_qs.aggregate(s=Sum("cost_usd"))["s"] or Decimal("0")
    run_cost = runs_qs.aggregate(s=Sum("cost_used_usd"))["s"] or Decimal("0")

    return {
        "date": target.isoformat(),
        "filter_classifications": filter_qs.count(),
        "filter_cost_usd": filter_cost,
        "agent_runs": runs_qs.count(),
        "agent_run_cost_usd": run_cost,
        "total_cost_usd": filter_cost + run_cost,
        "by_model": list(
            runs_qs.values("models_used").annotate(n=Count("id"), cost=Sum("cost_used_usd"))
        ),
    }
