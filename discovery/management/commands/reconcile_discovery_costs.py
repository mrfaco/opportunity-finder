"""Reconcile Ollama usage against checked published rates; dry-run by default."""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from discovery.accounting import reconcile_ollama_discovery
from discovery.models import Discovery


class Command(BaseCommand):
    help = "Estimate recorded Ollama Cloud token charges; no provider requests or billing receipts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--discovery", action="append", default=[])
        parser.add_argument("--all", action="store_true", dest="all_discoveries")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--verbose-operations", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        identifiers = options["discovery"]
        if bool(identifiers) == bool(options["all_discoveries"]):
            raise CommandError("Select one or more --discovery IDs, or --all")
        if options["all_discoveries"]:
            identifiers = list(
                Discovery.objects.filter(status__in=["completed", "failed", "cancelled"])
                .order_by("created_at")
                .values_list("pk", flat=True)
            )
        for identifier in identifiers:
            try:
                result = reconcile_ollama_discovery(str(identifier), apply=options["apply"])
            except (ValueError, Discovery.DoesNotExist) as exc:
                raise CommandError(str(exc)) from exc
            if not options["verbose_operations"]:
                result.pop("operations")
            self.stdout.write(json.dumps(result))
