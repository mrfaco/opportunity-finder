"""Celery application factory.

Tasks are auto-discovered from each installed app's ``tasks`` module.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("opportunity_finder")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
