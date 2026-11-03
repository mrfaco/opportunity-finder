"""Celery entry points backed by durable dispatch records."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from discovery.models import DiscoveryDispatch


@shared_task
def run_discovery_turn(task_id: str) -> None:
    from discovery.loop import run_task  # noqa: PLC0415  # runtime loads after task registration

    run_task(task_id)


@shared_task
def dispatch_pending() -> int:
    # Re-publication is safe: a task is claimed under a fenced lease. This also
    # repairs a crash between releasing a pending batch task and scheduling it.
    retry_before = timezone.now() - timedelta(seconds=60)
    DiscoveryDispatch.objects.filter(
        task__discovery__status__in=["queued", "running"],
    ).filter(
        Q(task__state="open", published_at__lt=retry_before)
        | Q(task__state="working", task__lease_expires_at__lt=timezone.now())
    ).update(published_at=None)
    count = 0
    for dispatch_id in DiscoveryDispatch.objects.filter(
        published_at__isnull=True,
        task__state__in=["open", "working"],
        task__discovery__status__in=["queued", "running"],
    ).values_list("id", flat=True):
        with transaction.atomic():
            row = (
                DiscoveryDispatch.objects.select_for_update(skip_locked=True)
                .filter(
                    pk=dispatch_id,
                    published_at__isnull=True,
                )
                .first()
            )
            if row is None:
                continue
            row.attempts = F("attempts") + 1
            row.save(update_fields=["attempts"])
            run_discovery_turn.apply_async(args=[str(row.task_id)])
            row.published_at = timezone.now()
            row.last_error = ""
            row.save(update_fields=["published_at", "last_error"])
            count += 1
    return count
