"""Tests for the cluster triage dashboard and its investigate action.

The triage view ranks un-investigated clusters and the investigate POST
handler kicks off a real ``start_run`` call (mocked in tests).
"""

from __future__ import annotations

import math
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)

_TRIAGE_URL = "/admin/clusters/triage/"


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_cluster(
    *,
    size: int,
    avg_conf: float,
    last_seen_days_ago: float,
    status: str = ClusterStatus.PENDING,
    last_investigated_at=None,
    title: str | None = None,
) -> Cluster:
    last_seen = timezone.now() - timedelta(days=last_seen_days_ago)
    cluster = Cluster.objects.create(
        status=status,
        size=size,
        first_seen_at=last_seen,
        last_seen_at=last_seen,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title=title,
        last_investigated_at=last_investigated_at,
    )
    for i in range(size):
        ClusterItem.objects.create(
            cluster=cluster,
            source=Source.HACKER_NEWS,
            source_item_id=f"{cluster.id}-{i}",
            url=f"https://example.com/{cluster.id}/{i}",
            title=f"item {i} in {cluster.id}",
            posted_at=last_seen,
            raw_text="x",
            snippet="x",
            classifier_verdict=ClassifierVerdict.OPPORTUNITY,
            classifier_confidence=avg_conf,
            embedding=_vec(),
            added_to_cluster_at=last_seen,
            assigned_at=last_seen,
        )
    return cluster


@pytest.fixture
def admin_client(db):
    user = get_user_model().objects.create_superuser(
        username="tester", email="t@example.com", password="x"
    )
    c = Client()
    c.force_login(user)
    return c


# ---------------------------------------------------------------------------
# Triage view
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_triage_renders(admin_client):
    a = _make_cluster(size=3, avg_conf=0.8, last_seen_days_ago=1, title="alpha")
    _make_cluster(size=1, avg_conf=0.9, last_seen_days_ago=0.5, title="beta")
    response = admin_client.get(_TRIAGE_URL)
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "alpha" in body
    assert "beta" in body
    # Action form points at the per-cluster investigate URL.
    assert f"/admin/clusters/cluster/{a.id}/investigate/" in body


@pytest.mark.django_db
def test_triage_excludes_investigated_by_default(admin_client):
    _make_cluster(size=2, avg_conf=0.8, last_seen_days_ago=1, title="live-one")
    _make_cluster(
        size=2,
        avg_conf=0.8,
        last_seen_days_ago=1,
        title="done-one",
        last_investigated_at=timezone.now(),
    )

    default = admin_client.get(_TRIAGE_URL).content.decode("utf-8")
    assert "live-one" in default
    assert "done-one" not in default

    all_view = admin_client.get(_TRIAGE_URL + "?show=all").content.decode("utf-8")
    assert "live-one" in all_view
    assert "done-one" in all_view


@pytest.mark.django_db
def test_triage_excludes_inactive_status(admin_client):
    _make_cluster(
        size=2,
        avg_conf=0.9,
        last_seen_days_ago=1,
        title="discarded-one",
        status=ClusterStatus.DISCARDED,
    )
    _make_cluster(
        size=2,
        avg_conf=0.9,
        last_seen_days_ago=1,
        title="live-one",
    )
    body = admin_client.get(_TRIAGE_URL).content.decode("utf-8")
    assert "live-one" in body
    assert "discarded-one" not in body


@pytest.mark.django_db
def test_triage_ranks_by_priority(admin_client):
    # The formula: log2(1 + size) * avg_conf * exp(-days / 7).
    _make_cluster(size=10, avg_conf=0.7, last_seen_days_ago=14, title="big-stale")
    _make_cluster(size=2, avg_conf=0.95, last_seen_days_ago=0.1, title="small-fresh-strong")
    _make_cluster(size=4, avg_conf=0.6, last_seen_days_ago=3, title="medium-meh")

    # Hand-compute expected ordering.
    def score(size, conf, days):
        return math.log2(1 + size) * conf * math.exp(-days / 7.0)

    expected_order = sorted(
        [
            ("big-stale", score(10, 0.7, 14)),
            ("small-fresh-strong", score(2, 0.95, 0.1)),
            ("medium-meh", score(4, 0.6, 3)),
        ],
        key=lambda x: x[1],
        reverse=True,
    )

    body = admin_client.get(_TRIAGE_URL).content.decode("utf-8")
    indices = [(name, body.find(name)) for name, _ in expected_order]
    assert all(idx > 0 for _, idx in indices), indices
    assert indices == sorted(indices, key=lambda x: x[1]), (
        f"Ranking mismatch. Expected: {[n for n, _ in expected_order]}. "
        f"Got order in HTML: {sorted(indices, key=lambda x: x[1])}"
    )


# ---------------------------------------------------------------------------
# Investigate action
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_investigate_calls_orchestrator_and_redirects(admin_client, monkeypatch):
    cluster = _make_cluster(size=2, avg_conf=0.8, last_seen_days_ago=1)
    captured: dict = {}
    fake_run_id = uuid.uuid4()

    def fake_start_run(cluster_id, agent_name, **kwargs):
        captured["cluster_id"] = cluster_id
        captured["agent_name"] = agent_name
        return fake_run_id

    monkeypatch.setattr("clusters.admin.start_run", fake_start_run)

    response = admin_client.post(f"/admin/clusters/cluster/{cluster.id}/investigate/")
    assert response.status_code == 302
    assert response["Location"] == f"/admin/agents/run/{fake_run_id}/trajectory/"
    assert str(captured["cluster_id"]) == str(cluster.id)
    assert captured["agent_name"] == "investigation"


@pytest.mark.django_db
def test_investigate_rejects_get(admin_client, monkeypatch):
    cluster = _make_cluster(size=1, avg_conf=0.7, last_seen_days_ago=1)
    called: dict = {"n": 0}
    monkeypatch.setattr(
        "clusters.admin.start_run",
        lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or uuid.uuid4(),
    )
    response = admin_client.get(f"/admin/clusters/cluster/{cluster.id}/investigate/")
    assert response.status_code == 405
    assert called["n"] == 0


@pytest.mark.django_db
def test_investigate_404_for_unknown_cluster(admin_client, monkeypatch):
    monkeypatch.setattr("clusters.admin.start_run", lambda *a, **kw: uuid.uuid4())
    response = admin_client.post(f"/admin/clusters/cluster/{uuid.uuid4()}/investigate/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Detail pages — regression guard for the numpy-array readonly-field crash.
# Django's display_for_field does ``value in field.empty_values`` on every
# readonly field; numpy arrays raise "truth value is ambiguous" there. We
# render embeddings via a string-returning method instead.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cluster_detail_page_renders(admin_client):
    cluster = _make_cluster(size=2, avg_conf=0.8, last_seen_days_ago=1, title="detail-test")
    response = admin_client.get(f"/admin/clusters/cluster/{cluster.id}/change/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "detail-test" in body
    # The centroid summary should appear in place of the raw vector.
    assert "-dim:" in body
    # The items inline should list each member with a link to the source.
    for item in cluster.items.all():
        assert f'href="{item.url}"' in body
        assert item.title in body


@pytest.mark.django_db
def test_cluster_item_detail_page_renders(admin_client):
    cluster = _make_cluster(size=1, avg_conf=0.8, last_seen_days_ago=1)
    item = cluster.items.first()
    response = admin_client.get(f"/admin/clusters/clusteritem/{item.id}/change/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "-dim:" in body


# ---------------------------------------------------------------------------
# Admin landing page — /admin/ should serve the triage queue, not the
# default Django app/model directory.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_admin_root_serves_triage(admin_client):
    _make_cluster(size=2, avg_conf=0.8, last_seen_days_ago=1, title="lands-on-triage")
    response = admin_client.get("/admin/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Cluster triage" in body
    assert "lands-on-triage" in body


@pytest.mark.django_db
def test_sidebar_includes_triage_shortcut(admin_client):
    """The left nav on any admin page should link to the triage queue."""
    # Use a model admin changelist (any page that renders the sidebar).
    response = admin_client.get("/admin/agents/agentrun/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Triage queue" in body
    assert "/admin/clusters/triage/" in body
