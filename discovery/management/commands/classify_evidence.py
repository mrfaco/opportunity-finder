"""Apply the current source-type rules to evidence classified under other rules."""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from discovery.models import Evidence
from discovery.source_types import classify, load_rules


class Command(BaseCommand):
    help = (
        "Recompute Evidence.source_type for rows whose rules version differs from the "
        "current one. Dry run by default; --apply writes."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Write the new types")

    def handle(self, *args: Any, **options: Any) -> None:
        version = load_rules().rules_version
        stale = Evidence.objects.exclude(
            metadata__contains={"source_type_rules_version": version}
        ).order_by("retrieved_at", "id")
        changes: Counter[tuple[str, str]] = Counter()
        examined = 0
        if options["apply"]:
            with transaction.atomic():
                for row in stale.select_for_update().iterator():
                    examined += 1
                    result = classify(row.connector, row.kind, row.url)
                    if result.source_type != row.source_type:
                        changes[(row.source_type, result.source_type)] += 1
                    # Backfilled rows keep their schema_version; the metadata records
                    # which rules classified them.
                    row.source_type = result.source_type
                    row.metadata = {
                        **row.metadata,
                        "source_type_rule": result.rule,
                        "source_type_rules_version": result.rules_version,
                    }
                    row.save(update_fields=["source_type", "metadata"])
        else:
            # A dry run reads without row locks so it never blocks live research writes.
            for row in stale.iterator():
                examined += 1
                result = classify(row.connector, row.kind, row.url)
                if result.source_type != row.source_type:
                    changes[(row.source_type, result.source_type)] += 1
        mode = "Applied" if options["apply"] else "Dry run (nothing written)"
        self.stdout.write(
            f"{mode}: rules {version}; {examined} row(s) classified under other rules; "
            f"{sum(changes.values())} {'changed' if options['apply'] else 'would change'} type."
        )
        by_type: Counter[str] = Counter()
        for (_, new), count in changes.items():
            by_type[new] += count
        for name, count in sorted(by_type.items()):
            self.stdout.write(f"  -> {name}: {count}")
        for (old, new), count in sorted(changes.items()):
            self.stdout.write(f"  {old} -> {new}: {count}")
