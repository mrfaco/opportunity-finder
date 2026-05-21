"""Seed the django-celery-beat periodic tasks.

The ``celery_beat`` service uses the database scheduler, so periodic tasks
live in DB rows rather than in settings. This command creates (or updates)
the schedules the system needs:

* hourly Hacker News ingestion,
* nightly cluster refinement.

Idempotent — safe to run after every deploy. Run once after ``migrate``:

    python manage.py setup_schedules
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Create or update the Celery Beat periodic tasks."

    def handle(self, *args: Any, **options: Any) -> None:
        hourly, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
        nightly, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="3", day_of_week="*", day_of_month="*", month_of_year="*"
        )

        PeriodicTask.objects.update_or_create(
            name="Ingest Hacker News",
            defaults={
                "task": "ingestion.tasks.ingest_source",
                "interval": hourly,
                "crontab": None,
                "args": json.dumps(["hacker_news"]),
                "enabled": True,
            },
        )
        PeriodicTask.objects.update_or_create(
            name="Refine clusters nightly",
            defaults={
                "task": "clusters.tasks.refine_clusters_nightly",
                "crontab": nightly,
                "interval": None,
                "args": json.dumps([]),
                "enabled": True,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Periodic tasks ready: 'Ingest Hacker News' (hourly), "
                "'Refine clusters nightly' (03:00 UTC)."
            )
        )
