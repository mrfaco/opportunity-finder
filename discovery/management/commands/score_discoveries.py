"""Score terminal discoveries and persist one comparable DiscoveryQualityRun."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from discovery.models import Discovery, DiscoveryQualityRun
from discovery.orchestrator import TERMINAL_STATUSES, content_hash
from discovery.quality import (
    CATEGORICAL_METRICS,
    METRICS_SCHEMA_VERSION,
    QUANTITATIVE_METRICS,
    aggregate_metrics,
    compute_quality,
    distinct_prompt_hashes,
    metric_deltas,
)


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.4g}"


def _config_hashes(ids: list[str]) -> set[str]:
    return {
        content_hash(config)
        for config in Discovery.objects.filter(pk__in=ids).values_list("config_snapshot", flat=True)
    }


class Command(BaseCommand):
    help = "Compute quality metrics for terminal discoveries and persist the scoring pass."

    def add_arguments(self, parser: CommandParser) -> None:
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument("--tag", help="Score discoveries with this benchmark_tag")
        selection.add_argument("--discovery", action="append", help="Discovery ID (repeatable)")
        parser.add_argument("--since", type=date.fromisoformat, help="Created on/after (ISO)")
        parser.add_argument("--compare-to", type=uuid.UUID, help="Earlier quality run ID")
        parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["tag"]:
            queryset = Discovery.objects.filter(benchmark_tag=options["tag"])
        else:
            try:
                queryset = Discovery.objects.filter(pk__in=options["discovery"])
                missing = set(options["discovery"]) - {
                    str(pk) for pk in queryset.values_list("pk", flat=True)
                }
            except ValidationError as exc:
                raise CommandError(f"Invalid discovery ID: {exc}") from exc
            if missing:
                raise CommandError(f"Unknown discovery IDs: {sorted(missing)}")
        if options["since"]:
            queryset = queryset.filter(
                created_at__gte=timezone.make_aware(datetime.combine(options["since"], time.min))
            )
        earlier = None
        if options["compare_to"]:
            earlier = DiscoveryQualityRun.objects.filter(pk=options["compare_to"]).first()
            if earlier is None:
                raise CommandError(f"Unknown quality run {options['compare_to']}")
        selected = list(queryset.order_by("created_at", "id"))
        terminal = [row for row in selected if row.status in TERMINAL_STATUSES]
        skipped = [row for row in selected if row.status not in TERMINAL_STATUSES]
        if not terminal:
            raise CommandError(
                f"No terminal discoveries to score ({len(skipped)} selected are still running); "
                "nothing was written."
            )
        metrics = [compute_quality(row) for row in terminal]
        run = DiscoveryQualityRun.objects.create(
            tag=options["tag"] or "",
            discovery_ids=[str(row.pk) for row in terminal],
            metrics_schema_version=METRICS_SCHEMA_VERSION,
            aggregate=aggregate_metrics(metrics),
            per_discovery=[row.model_dump(mode="json") for row in metrics],
            prompt_hashes=distinct_prompt_hashes(metrics),
            config_snapshots_distinct=len({content_hash(row.config_snapshot) for row in terminal}),
        )
        skipped_lines = [f"Skipped {row.pk}: {row.status}, not terminal." for row in skipped]
        if options["format"] == "json":
            for line in skipped_lines:
                self.stderr.write(line)
            self.stdout.write(json.dumps(run.as_dict(), indent=2, sort_keys=True))
            return
        self._markdown(run, skipped_lines, earlier)

    def _markdown(
        self,
        run: DiscoveryQualityRun,
        skipped_lines: list[str],
        earlier: DiscoveryQualityRun | None,
    ) -> None:
        write = self.stdout.write
        write(f"## Quality run `{run.pk}`")
        write("")
        write(
            f"Tag: {run.tag or '—'} · Scored: {len(run.discovery_ids)} · "
            f"Metrics schema: {run.metrics_schema_version} · "
            f"Distinct configs: {run.config_snapshots_distinct} · "
            f"Created: {run.created_at.isoformat()}"
        )
        write("")
        for role, hashes in run.prompt_hashes.items():
            write(f"- Prompt `{role}`: {', '.join(f'`{value}`' for value in hashes)}")
        write("")
        write("| Metric | Mean | Median | Min | Max | Sum | n | None |")
        write("|---|---|---|---|---|---|---|---|")
        for name in QUANTITATIVE_METRICS:
            stats = run.aggregate[name]
            write(
                f"| {name} | {_number(stats['mean'])} | {_number(stats['median'])} | "
                f"{_number(stats['min'])} | {_number(stats['max'])} | {_number(stats['sum'])} | "
                f"{stats['count']} | {stats['none_count']} |"
            )
        write("")
        for name in CATEGORICAL_METRICS:
            stats = run.aggregate[name]
            counts = ", ".join(f"{key}: {value}" for key, value in stats["distribution"].items())
            write(f"- {name}: {counts or '—'}; none: {stats['none_count']}")
        if skipped_lines:
            write("")
            for line in skipped_lines:
                write(f"- {line}")
        if earlier is None:
            return
        write("")
        write(f"### Compared with `{earlier.pk}` ({earlier.tag or '—'})")
        write("")
        if earlier.metrics_schema_version != run.metrics_schema_version:
            write(
                f"WARNING: metrics schema changed ({earlier.metrics_schema_version} → "
                f"{run.metrics_schema_version}); changed metrics are not comparable."
            )
        if earlier.prompt_hashes != run.prompt_hashes:
            write(
                "WARNING: prompt hashes differ between the runs; deltas compare different prompts."
            )
        if _config_hashes(earlier.discovery_ids) != _config_hashes(run.discovery_ids):
            write("WARNING: discovery configurations differ between the runs.")
        write("")
        write("| Metric | Earlier mean | Current mean | Delta |")
        write("|---|---|---|---|")
        for name, before, after, delta in metric_deltas(earlier.aggregate, run.aggregate):
            sign = "+" if delta is not None and delta > 0 else ""
            write(f"| {name} | {_number(before)} | {_number(after)} | {sign}{_number(delta)} |")
