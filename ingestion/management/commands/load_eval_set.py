"""Load the hand-curated classifier eval set from JSON fixtures.

The seed eval set lives in ``ingestion/eval_data/*.json`` — one file per
difficulty tier, version-controlled like the prompts (it is content with
versioning needs). This command upserts those items into ``FilterEvalSet``.

Idempotent: an item is matched by exact ``content``. Re-running updates the
labels/metadata of existing rows and inserts new ones. ``--flush`` deletes
all hand-curated items first for a clean reload.

    python manage.py load_eval_set
    python manage.py load_eval_set --flush
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from ingestion.models import (
    DifficultyTier,
    FilterEvalSet,
    HumanConfidence,
    HumanLabel,
    SourcedFrom,
)

EVAL_DATA_DIR = Path(__file__).resolve().parents[2] / "eval_data"

_REQUIRED_FIELDS = {
    "content",
    "source",
    "human_label",
    "human_confidence",
    "difficulty_tier",
}
_VALID_LABELS = {c.value for c in HumanLabel}
_VALID_CONFIDENCE = {c.value for c in HumanConfidence}
_VALID_TIERS = {c.value for c in DifficultyTier}


def _validate(item: dict[str, Any], path: Path, index: int) -> None:
    """Raise CommandError on any malformed item — no silent skips."""
    where = f"{path.name}[{index}]"
    missing = _REQUIRED_FIELDS - item.keys()
    if missing:
        raise CommandError(f"{where}: missing required field(s): {sorted(missing)}")
    if item["human_label"] not in _VALID_LABELS:
        raise CommandError(
            f"{where}: human_label {item['human_label']!r} not in {sorted(_VALID_LABELS)}"
        )
    if item["human_confidence"] not in _VALID_CONFIDENCE:
        raise CommandError(
            f"{where}: human_confidence {item['human_confidence']!r} "
            f"not in {sorted(_VALID_CONFIDENCE)}"
        )
    if item["difficulty_tier"] not in _VALID_TIERS:
        raise CommandError(
            f"{where}: difficulty_tier {item['difficulty_tier']!r} "
            f"not in {sorted(_VALID_TIERS)}"
        )
    if not str(item["content"]).strip():
        raise CommandError(f"{where}: content is empty")


class Command(BaseCommand):
    help = "Load the hand-curated classifier eval set from ingestion/eval_data/."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all hand-curated eval items before loading.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not EVAL_DATA_DIR.is_dir():
            raise CommandError(f"Eval data directory not found: {EVAL_DATA_DIR}")

        files = sorted(EVAL_DATA_DIR.glob("*.json"))
        if not files:
            raise CommandError(f"No *.json fixtures in {EVAL_DATA_DIR}")

        created = 0
        updated = 0

        with transaction.atomic():
            if options["flush"]:
                deleted, _ = FilterEvalSet.objects.filter(
                    sourced_from=SourcedFrom.HAND_CURATED
                ).delete()
                self.stdout.write(f"Flushed {deleted} hand-curated eval item(s).")

            for path in files:
                items = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(items, list):
                    raise CommandError(f"{path.name}: top level must be a JSON array")
                for index, item in enumerate(items):
                    _validate(item, path, index)
                    defaults = {
                        "content_context": item.get("content_context", {}),
                        "source": item["source"],
                        "human_label": item["human_label"],
                        "human_confidence": item["human_confidence"],
                        "difficulty_tier": item["difficulty_tier"],
                        "reasoning_note": item.get("reasoning_note", ""),
                        "sourced_from": SourcedFrom.HAND_CURATED,
                    }
                    _, was_created = FilterEvalSet.objects.update_or_create(
                        content=item["content"],
                        defaults=defaults,
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

        total = FilterEvalSet.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Eval set loaded: {created} created, {updated} updated, "
                f"{total} total in FilterEvalSet."
            )
        )
