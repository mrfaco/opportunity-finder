"""Tests for the ingestion operations dashboard."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from ingestion.models import IngestionCheckpoint

_OPS_URL = "/admin/ingestion/operations/"


@pytest.fixture
def admin_client(db):
    user = get_user_model().objects.create_superuser(
        username="tester", email="t@example.com", password="x"
    )
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_operations_view_lists_registered_sources(admin_client):
    IngestionCheckpoint.objects.create(
        source="hacker_news",
        last_item_posted_at=timezone.now() - timedelta(hours=2),
        last_run_at=timezone.now() - timedelta(hours=1),
        items_seen=42,
        opportunities_found=7,
    )
    response = admin_client.get(_OPS_URL)
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Ingestion operations" in body
    assert "hacker_news" in body
    assert "42" in body  # items_seen
    assert "7" in body  # opportunities_found
    assert "/admin/ingestion/operations/hacker_news/ingest/" in body
    assert "/admin/ingestion/operations/hacker_news/backfill/" in body


@pytest.mark.django_db
def test_operations_view_renders_without_checkpoint_rows(admin_client):
    """Empty DB — the row should still appear with zero stats."""
    response = admin_client.get(_OPS_URL)
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "hacker_news" in body
    assert "never" in body  # last_run_at default


@pytest.mark.django_db
def test_trigger_ingest_enqueues_celery_task(admin_client, monkeypatch):
    captured: dict = {}

    class FakeAsyncResult:
        id = "fake-task-id"

    def fake_delay(source):
        captured["source"] = source
        return FakeAsyncResult()

    monkeypatch.setattr("ingestion.tasks.ingest_source.delay", fake_delay)
    response = admin_client.post("/admin/ingestion/operations/hacker_news/ingest/")
    assert response.status_code == 302
    assert response["Location"] == _OPS_URL
    assert captured == {"source": "hacker_news"}


@pytest.mark.django_db
def test_trigger_ingest_rejects_get(admin_client):
    response = admin_client.get("/admin/ingestion/operations/hacker_news/ingest/")
    assert response.status_code == 405


@pytest.mark.django_db
def test_trigger_ingest_rejects_unknown_source(admin_client, monkeypatch):
    called: dict = {"n": 0}
    monkeypatch.setattr(
        "ingestion.tasks.ingest_source.delay",
        lambda s: called.__setitem__("n", called["n"] + 1),
    )
    response = admin_client.post("/admin/ingestion/operations/myspace/ingest/")
    assert response.status_code == 302
    assert called["n"] == 0


@pytest.mark.django_db
def test_trigger_backfill_enqueues_celery_task(admin_client, monkeypatch):
    captured: dict = {}

    class FakeAsyncResult:
        id = "fake-backfill-id"

    def fake_delay(source, days):
        captured["source"] = source
        captured["days"] = days
        return FakeAsyncResult()

    monkeypatch.setattr("ingestion.tasks.backfill_source_task.delay", fake_delay)
    response = admin_client.post(
        "/admin/ingestion/operations/hacker_news/backfill/", {"days": "14"}
    )
    assert response.status_code == 302
    assert captured == {"source": "hacker_news", "days": 14}


@pytest.mark.django_db
def test_trigger_backfill_rejects_bad_days(admin_client, monkeypatch):
    called: dict = {"n": 0}
    monkeypatch.setattr(
        "ingestion.tasks.backfill_source_task.delay",
        lambda s, d: called.__setitem__("n", called["n"] + 1),
    )
    for value in ("0", "-3", "not-a-number", ""):
        response = admin_client.post(
            "/admin/ingestion/operations/hacker_news/backfill/", {"days": value}
        )
        assert response.status_code == 302
    assert called["n"] == 0


@pytest.mark.django_db
def test_sidebar_includes_ingestion_ops_shortcut(admin_client):
    response = admin_client.get("/admin/agents/agentrun/")
    body = response.content.decode("utf-8")
    assert "Ingestion ops" in body
    assert "/admin/ingestion/operations/" in body


@pytest.mark.django_db
def test_backfill_from_adapter_dedups_and_returns_stats(monkeypatch):
    """The extracted ``backfill_from_adapter`` function preserves the
    behaviour the management command had inline."""
    from datetime import UTC, datetime

    from clusters.models import (
        EMBEDDING_DIM,
        ClassifierVerdict,
        Cluster,
        ClusterItem,
        ClusterStatus,
        Source,
    )
    from ingestion import backfill, pipeline
    from ingestion.adapters.base import IngestedItem

    now = timezone.now()
    cluster = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=[0.01] * EMBEDDING_DIM,
    )
    ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id="seen-1",
        url="https://example.com/seen",
        posted_at=now,
        raw_text="x",
        snippet="x",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=0.9,
        embedding=[0.01] * EMBEDDING_DIM,
        added_to_cluster_at=now,
        assigned_at=now,
    )

    class _StubAdapter:
        source = "hacker_news"

        def fetch_new_items(self, since=None):  # noqa: ARG002
            return iter(
                [
                    IngestedItem(
                        source="hacker_news",
                        source_item_id="seen-1",  # already in DB → skipped
                        url="https://example.com/seen",
                        title="duplicate",
                        author="x",
                        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
                        raw_text="dup",
                        metadata={},
                    ),
                    IngestedItem(
                        source="hacker_news",
                        source_item_id="new-1",
                        url="https://example.com/new",
                        title="brand new",
                        author="x",
                        posted_at=datetime(2026, 1, 2, tzinfo=UTC),
                        raw_text="new",
                        metadata={},
                    ),
                ]
            )

    # ``backfill`` imports ``_process_item`` at module level — patch the name
    # in the backfill module, not the pipeline module.
    monkeypatch.setattr(backfill, "_process_item", lambda item: True)
    _ = pipeline  # imported for the symmetric "this is the same thing" hint
    stats = backfill.backfill_from_adapter(_StubAdapter(), days=30)
    assert stats == {
        "source": "hacker_news",
        "fetched": 2,
        "processed": 1,
        "opportunities": 1,
        "discarded": 0,
    }


def test_backfill_from_adapter_rejects_nonpositive_days():
    from ingestion import backfill

    class _StubAdapter:
        source = "hacker_news"

        def fetch_new_items(self, since=None):  # pragma: no cover - never called
            return iter([])

    with pytest.raises(ValueError, match="days must be positive"):
        backfill.backfill_from_adapter(_StubAdapter(), days=0)
