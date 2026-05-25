"""Tests for the Latest investigations dashboard."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from agents.models import AgentRun, AgentRunStatus
from clusters.models import (
    EMBEDDING_DIM,
    Cluster,
    ClusterStatus,
    Source,
)
from investigations.models import Investigation, InvestigationStatus

_LATEST_URL = "/admin/investigations/latest/"


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_cluster(title: str) -> Cluster:
    now = timezone.now()
    return Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title=title,
    )


def _make_run(cluster: Cluster, cost: str = "0.05", steps: int = 8) -> AgentRun:
    return AgentRun.objects.create(
        agent_name="investigation",
        cluster=cluster,
        trigger="manual",
        status=AgentRunStatus.COMPLETED,
        steps_used=steps,
        cost_used_usd=cost,
        budget_max_steps=30,
        budget_max_cost_usd="0.50",
        budget_max_duration_s=300,
        models_used=["claude-sonnet-4-6"],
        config_snapshot={},
        cluster_snapshot={},
        prompt_hashes={},
    )


def _make_investigation(
    cluster: Cluster,
    *,
    headline: str = "An opportunity",
    confidence: float = 0.7,
    status: str = InvestigationStatus.AWAITING_REVIEW,
    created_offset: timedelta = timedelta(0),
) -> Investigation:
    run = _make_run(cluster)
    inv = Investigation.objects.create(
        cluster=cluster,
        status=status,
        primary_run=run,
        brief={
            "headline": headline,
            "problem_statement": f"A specific pain around {headline}.",
            "target_user": "small SaaS teams",
            "confidence": confidence,
        },
        cluster_snapshot={"id": str(cluster.id), "size": cluster.size},
    )
    if created_offset != timedelta(0):
        Investigation.objects.filter(pk=inv.pk).update(created_at=timezone.now() - created_offset)
        inv.refresh_from_db()
    return inv


@pytest.fixture
def admin_client(db):
    user = get_user_model().objects.create_superuser(
        username="tester", email="t@example.com", password="x"
    )
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_latest_view_renders_with_no_investigations(admin_client):
    response = admin_client.get(_LATEST_URL)
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Latest investigations" in body
    assert "No investigations yet" in body


@pytest.mark.django_db
def test_latest_view_lists_briefs_newest_first(admin_client):
    c1 = _make_cluster("cluster-old")
    c2 = _make_cluster("cluster-new")
    _make_investigation(c1, headline="OLD-headline", created_offset=timedelta(days=2))
    _make_investigation(c2, headline="NEW-headline", created_offset=timedelta(minutes=1))

    body = admin_client.get(_LATEST_URL).content.decode("utf-8")
    new_idx = body.find("NEW-headline")
    old_idx = body.find("OLD-headline")
    assert new_idx > 0 and old_idx > 0
    assert new_idx < old_idx, "newest should appear before older"
    # Headline + problem + target_user surfaced in the row.
    assert "small SaaS teams" in body
    assert "0.70" in body  # confidence


@pytest.mark.django_db
def test_latest_view_status_filter(admin_client):
    cluster = _make_cluster("c")
    _make_investigation(cluster, headline="AR-headline", status=InvestigationStatus.AWAITING_REVIEW)
    _make_investigation(
        _make_cluster("c2"), headline="PROMOTED-headline", status=InvestigationStatus.PROMOTED
    )

    awaiting = admin_client.get(_LATEST_URL + "?status=awaiting_review").content.decode("utf-8")
    assert "AR-headline" in awaiting
    assert "PROMOTED-headline" not in awaiting

    promoted = admin_client.get(_LATEST_URL + "?status=promoted").content.decode("utf-8")
    assert "PROMOTED-headline" in promoted
    assert "AR-headline" not in promoted


@pytest.mark.django_db
def test_sidebar_includes_latest_investigations_shortcut(admin_client):
    response = admin_client.get("/admin/agents/agentrun/")
    body = response.content.decode("utf-8")
    assert "Latest investigations" in body
    assert "/admin/investigations/latest/" in body
