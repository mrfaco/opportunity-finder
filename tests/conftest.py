"""Test fixtures shared across smoke tests.

These tests assume Postgres (+ pgvector) — they run inside the ``web``
container via ``make test``. A bootstrap migration creates the ``vector``
extension before any model migrations apply.
"""

from __future__ import annotations

import os

import django
import pytest


def pytest_configure() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")
    django.setup()


@pytest.fixture
def admin_user(db, django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )
