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
    """Smoke check that ``config/settings.py`` imports cleanly and the
    clustering knobs are in the sane cosine-similarity range. We do
    *not* pin exact values here — the defaults get retuned as the
    embedding model changes (see the threshold-tuning session in
    2026-05), and an environment with an explicit ``.env`` override
    is a legitimate state for this test to see."""
    from django.conf import settings

    assert 0.5 <= settings.CLUSTER_JOIN_THRESHOLD <= 1.0
    assert 0.5 <= settings.CLUSTER_MERGE_THRESHOLD <= 1.0
    # Join should be looser than merge: an item joins an existing cluster
    # at a lower similarity than two cluster centroids merging together.
    assert settings.CLUSTER_JOIN_THRESHOLD <= settings.CLUSTER_MERGE_THRESHOLD
