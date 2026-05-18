"""Smoke tests — project boots, admin renders."""

from __future__ import annotations

import pytest
from django.test import Client


@pytest.mark.django_db
def test_admin_index_renders(admin_user):
    client = Client()
    client.force_login(admin_user)
    response = client.get("/admin/")
    assert response.status_code == 200


def test_apps_loaded():
    from django.apps import apps

    for label in ("core", "clusters", "ingestion", "agents", "investigations", "notifications"):
        assert apps.is_installed(label), f"{label} not installed"


def test_settings_loaded():
    from django.conf import settings

    assert settings.CLUSTER_JOIN_THRESHOLD == 0.75
    assert settings.CLUSTER_MERGE_THRESHOLD == 0.82
