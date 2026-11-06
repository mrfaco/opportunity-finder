"""Start one discovery per benchmark objective; a dry run unless spend is authorized."""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from discovery.connectors.base import ConnectorConfigurationError
from discovery.eval.benchmark import load_benchmark
from discovery.models import Discovery
from discovery.orchestrator import DiscoveryError, resolve_config, start_discovery
from discovery.prompts import role_prompt

_TAG = re.compile(r"^[a-z0-9-]{1,64}$")


def _usd(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal amount: {value!r}") from exc
    if not amount.is_finite() or amount <= 0:
        raise argparse.ArgumentTypeError("must be a positive amount")
    return amount


def benchmark_key(tag: str, objective_id: str) -> str:
    return f"benchmark:{tag}:{objective_id}"


class Command(BaseCommand):
    help = (
        "Start the benchmark objectives under one tag and one frozen config. Prints the plan "
        "and starts nothing unless --authorize-spend and --max-total-usd are given."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tag", required=True, help="Benchmark tag, [a-z0-9-]+")
        parser.add_argument("--objective", action="append", default=[], help="Run a subset")
        parser.add_argument("--config-json", default="", help="Override over deployment defaults")
        parser.add_argument("--max-total-usd", type=_usd, default=None)
        parser.add_argument("--authorize-spend", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        tag = options["tag"]
        if not _TAG.match(tag):
            raise CommandError("--tag must match [a-z0-9-]+ and be at most 64 characters")
        objectives = load_benchmark().objectives
        known = {item.id for item in objectives}
        unknown = sorted(set(options["objective"]) - known)
        if unknown:
            raise CommandError(f"Unknown benchmark objective IDs: {unknown}")
        if options["objective"]:
            objectives = [item for item in objectives if item.id in options["objective"]]
        override: Any = {}
        if options["config_json"]:
            try:
                override = json.loads(options["config_json"])
            except json.JSONDecodeError as exc:
                raise CommandError(f"--config-json is not valid JSON: {exc}") from exc
            if not isinstance(override, dict):
                raise CommandError("--config-json must be a JSON object")
        try:
            config = resolve_config(override)
        except DiscoveryError as exc:
            raise CommandError(f"Invalid discovery config: {exc}") from exc
        roles = (
            ("finder", "validator", "judge") if config.judge_enabled else ("finder", "validator")
        )
        ceiling = config.max_cost_usd * len(objectives)
        self.stdout.write("Frozen config:")
        self.stdout.write(json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True))
        self.stdout.write("Prompt hashes:")
        for role in roles:
            self.stdout.write(f"  {role}: {role_prompt(role).hash}")
        self.stdout.write(f"Ceiling: {len(objectives)} × USD {config.max_cost_usd} = USD {ceiling}")
        existing = {
            row.idempotency_key: row
            for row in Discovery.objects.filter(
                idempotency_key__in=[benchmark_key(tag, item.id) for item in objectives]
            )
        }
        if not options["authorize_spend"]:
            for item in objectives:
                key = benchmark_key(tag, item.id)
                state = f"exists {existing[key].pk}" if key in existing else "planned"
                self.stdout.write(f"{key}  {state}")
            self.stdout.write(
                "Dry run: nothing started. Re-run with --authorize-spend "
                f"--max-total-usd {ceiling} to start."
            )
            return
        if options["max_total_usd"] is None:
            raise CommandError("--authorize-spend requires --max-total-usd")
        if ceiling > options["max_total_usd"]:
            raise CommandError(
                f"Refusing to start: the ceiling sum USD {ceiling} exceeds "
                f"--max-total-usd {options['max_total_usd']}. Nothing was started."
            )
        for item in objectives:
            key = benchmark_key(tag, item.id)
            if key in existing:
                self.stdout.write(f"{key}  exists {existing[key].pk}")
                continue
            try:
                discovery = start_discovery(
                    item.objective,
                    config=config,
                    constraints=item.constraints,
                    idempotency_key=key,
                    benchmark_tag=tag,
                    benchmark_objective_id=item.id,
                )
            except (DiscoveryError, ConnectorConfigurationError) as exc:
                raise CommandError(
                    f"{key} could not start: {exc}. Discoveries listed above were started; "
                    "re-running with the same tag will not start them again."
                ) from exc
            self.stdout.write(f"{key}  started {discovery.pk}")
