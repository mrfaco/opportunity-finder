"""Tests for the public HTTP API.

Covers authentication, the 13-endpoint surface, and the orchestrator
contract that the API delegates to (concurrency + state transitions).

Celery tasks are stubbed at their module-level import sites so triggers
return a queued response without actually running the adapter body.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from agents.models import (
    AgentRun,
    AgentRunStatus,
    AgentRunTrigger,
)
from api.models import ApiKey, generate_raw_key, hash_key
from clusters.models import EMBEDDING_DIM, Cluster, ClusterItem, ClusterStatus, Source
from ideation.models import Ideation, IdeationStatus
from investigations.models import Investigation, InvestigationStatus, StaleReason

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


@pytest.fixture
def api_user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="apiuser", email="apiuser@example.com", password="pass"
    )


@pytest.fixture
def api_key_and_raw(api_user):
    key, raw = ApiKey.create_for_user(user=api_user, label="test")
    return key, raw


@pytest.fixture
def auth_headers(api_key_and_raw):
    _, raw = api_key_and_raw
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture
def cluster(db):
    now = timezone.now()
    return Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=3,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title="A cluster",
        summary="Some summary",
        category_tags=["dev-tools"],
        classifier_score=0.8,
    )


def _make_investigation(
    cluster: Cluster, *, status=InvestigationStatus.AWAITING_REVIEW
) -> Investigation:
    run = AgentRun.objects.create(
        agent_name="investigation",
        cluster=cluster,
        trigger=AgentRunTrigger.MANUAL,
        status=AgentRunStatus.COMPLETED,
        budget_max_steps=10,
        budget_max_cost_usd=Decimal("0.50"),
        budget_max_duration_s=300,
        models_used=["claude-sonnet-4-6"],
        config_snapshot={},
        cluster_snapshot={"id": str(cluster.id), "title": cluster.title},
    )
    return Investigation.objects.create(
        cluster=cluster,
        primary_run=run,
        status=status,
        brief={
            "schema_version": "1.0",
            "headline": "An opportunity",
            "problem_statement": "A problem.",
            "target_user": "Some user",
            "evidence_summary": "Evidence.",
            "evidence": [],
            "competitors": [],
            "differentiators": ["x"],
            "risks": ["y"],
            "confidence": 0.7,
            "recommended_next_step": "Talk to people.",
        },
        cluster_snapshot={"id": str(cluster.id)},
    )


def _stub_noop_delay(monkeypatch, dotted_path: str) -> list[tuple]:
    """Replace a Celery task's ``.delay`` with a no-op that records calls.

    Returns the list the stub appends to so the caller can assert on it.
    """
    calls: list[tuple] = []

    class _NoOpTask:
        def delay(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(id=str(uuid4()))

    module_path, _, attr = dotted_path.rpartition(".")
    import importlib  # noqa: PLC0415  # localised — only used by this helper

    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, attr, _NoOpTask())
    return calls


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_authorization_header_returns_401(client):
    resp = client.get("/api/v1/investigations/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_malformed_authorization_returns_401(client):
    resp = client.get("/api/v1/investigations/", HTTP_AUTHORIZATION="Bearer too many parts")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_invalid_key_returns_401(client):
    resp = client.get("/api/v1/investigations/", HTTP_AUTHORIZATION="Bearer opp_doesnotexist1234")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_revoked_key_returns_401(client, api_key_and_raw):
    key, raw = api_key_and_raw
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])
    resp = client.get("/api/v1/investigations/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_inactive_user_returns_401(client, api_user, api_key_and_raw):
    _, raw = api_key_and_raw
    api_user.is_active = False
    api_user.save(update_fields=["is_active"])
    resp = client.get("/api/v1/investigations/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_valid_key_updates_last_used_at(client, api_key_and_raw, auth_headers):
    key, _ = api_key_and_raw
    assert key.last_used_at is None
    resp = client.get("/api/v1/investigations/", **auth_headers)
    assert resp.status_code == 200
    key.refresh_from_db()
    assert key.last_used_at is not None


def test_hash_key_is_deterministic_and_unique():
    raw_a = generate_raw_key()
    raw_b = generate_raw_key()
    assert raw_a != raw_b
    assert hash_key(raw_a) == hash_key(raw_a)
    assert hash_key(raw_a) != hash_key(raw_b)
    assert raw_a.startswith("opp_")


# ---------------------------------------------------------------------------
# Ingestion endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ingestion_trigger_enqueues_task(client, auth_headers, monkeypatch):
    calls = _stub_noop_delay(monkeypatch, "ingestion.tasks.ingest_source")
    resp = client.post(
        "/api/v1/ingestion/runs/",
        data={"source": "hacker_news"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert resp.json()["task_id"]
    assert calls == [(("hacker_news",), {})]


@pytest.mark.django_db
def test_ingestion_trigger_unknown_source_400(client, auth_headers):
    resp = client.post(
        "/api/v1/ingestion/runs/",
        data={"source": "twitter"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_ingestion_backfill_enqueues_task(client, auth_headers, monkeypatch):
    calls = _stub_noop_delay(monkeypatch, "ingestion.tasks.backfill_source_task")
    resp = client.post(
        "/api/v1/ingestion/backfills/",
        data={"source": "github", "days": 7},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    assert calls == [(("github", 7), {})]


@pytest.mark.django_db
def test_ingestion_backfill_rejects_zero_days(client, auth_headers):
    resp = client.post(
        "/api/v1/ingestion/backfills/",
        data={"source": "github", "days": 0},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_ingestion_items_lists_recent(client, auth_headers, cluster):
    ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id="42",
        url="https://example.com/42",
        title="Some title",
        posted_at=timezone.now(),
        raw_text="text",
        snippet="snippet",
        classifier_verdict="opportunity",
        classifier_confidence=0.9,
        embedding=_vec(),
        added_to_cluster_at=timezone.now(),
        assigned_at=timezone.now(),
    )
    resp = client.get("/api/v1/ingestion/items/", **auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["source_item_id"] == "42"
    assert rows[0]["cluster_id"] == str(cluster.id)


@pytest.mark.django_db
def test_ingestion_runs_history_filters_by_task_name(client, auth_headers):
    from django_celery_results.models import TaskResult  # noqa: PLC0415

    TaskResult.objects.create(
        task_id="t1",
        task_name="ingestion.tasks.ingest_source",
        status="SUCCESS",
        task_args="('hacker_news',)",
    )
    TaskResult.objects.create(
        task_id="t2",
        task_name="agents.tasks.run_agent_loop",  # NOT ingestion
        status="SUCCESS",
    )
    resp = client.get("/api/v1/ingestion/runs/", **auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    task_ids = [r["task_id"] for r in rows]
    assert "t1" in task_ids
    assert "t2" not in task_ids


# ---------------------------------------------------------------------------
# Clusters endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clusters_list_filters_by_min_size(client, auth_headers, cluster):
    small = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title="Small one",
        classifier_score=0.5,
    )
    resp = client.get("/api/v1/clusters/?min_size=3", **auth_headers)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert str(cluster.id) in ids
    assert str(small.id) not in ids


@pytest.mark.django_db
def test_clusters_excludes_merged_clusters(client, auth_headers, cluster):
    Cluster.objects.create(
        status=ClusterStatus.MERGED_INTO,
        size=5,
        first_seen_at=timezone.now(),
        last_seen_at=timezone.now(),
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title="Merged ghost",
        classifier_score=0.5,
    )
    resp = client.get("/api/v1/clusters/", **auth_headers)
    assert resp.status_code == 200
    titles = [r["title"] for r in resp.json()]
    assert "Merged ghost" not in titles
    assert "A cluster" in titles


# ---------------------------------------------------------------------------
# Investigations endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_investigation_runs_post_enqueues(client, auth_headers, cluster, monkeypatch):
    # start_investigation → start_run → run_agent_loop.delay. Stub the inner task.
    _stub_noop_delay(monkeypatch, "agents.orchestrator.run_agent_loop")
    resp = client.post(
        "/api/v1/investigations/runs/",
        data={"cluster_id": str(cluster.id)},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["cluster_id"] == str(cluster.id)
    assert body["status"] == "queued"
    # An AgentRun row was created by start_run.
    assert AgentRun.objects.filter(cluster=cluster, agent_name="investigation").exists()


@pytest.mark.django_db
def test_investigation_runs_post_404_for_missing_cluster(client, auth_headers):
    resp = client.post(
        "/api/v1/investigations/runs/",
        data={"cluster_id": "11111111-1111-1111-1111-111111111111"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_investigations_list_filters_by_status(client, auth_headers, cluster):
    awaiting = _make_investigation(cluster, status=InvestigationStatus.AWAITING_REVIEW)
    _make_investigation(cluster, status=InvestigationStatus.REJECTED)

    resp = client.get("/api/v1/investigations/?status=awaiting_review", **auth_headers)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert str(awaiting.id) in ids
    assert len(ids) == 1


@pytest.mark.django_db
def test_investigation_detail_returns_full_brief(client, auth_headers, cluster):
    inv = _make_investigation(cluster)
    resp = client.get(f"/api/v1/investigations/{inv.id}/", **auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(inv.id)
    # The full brief JSON is exposed (not just the headline).
    assert body["brief"]["problem_statement"] == "A problem."
    assert body["brief_schema_version"] == "1.0"


@pytest.mark.django_db
def test_investigation_detail_404(client, auth_headers):
    resp = client.get(
        "/api/v1/investigations/11111111-1111-1111-1111-111111111111/", **auth_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Promotion + reject + stale
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_promote_flips_status_and_enqueues_ideation(client, auth_headers, cluster, monkeypatch):
    _stub_noop_delay(monkeypatch, "agents.orchestrator.run_agent_loop")
    inv = _make_investigation(cluster)

    resp = client.post(
        f"/api/v1/investigations/{inv.id}/promote/",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["investigation"]["status"] == InvestigationStatus.PROMOTED
    assert body["ideation_id"]
    assert body["ideation_run_id"]

    inv.refresh_from_db()
    assert inv.status == InvestigationStatus.PROMOTED
    assert Ideation.objects.filter(investigation=inv, status=IdeationStatus.DRAFT).exists()


@pytest.mark.django_db
def test_promote_rejects_already_promoted_409(client, auth_headers, cluster):
    inv = _make_investigation(cluster, status=InvestigationStatus.PROMOTED)
    resp = client.post(
        f"/api/v1/investigations/{inv.id}/promote/",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["current_status"] == InvestigationStatus.PROMOTED
    assert body["expected_status"] == InvestigationStatus.AWAITING_REVIEW


@pytest.mark.django_db
def test_reject_flips_status(client, auth_headers, cluster):
    inv = _make_investigation(cluster)
    resp = client.post(
        f"/api/v1/investigations/{inv.id}/reject/",
        data={"reason": "duplicate"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    inv.refresh_from_db()
    assert inv.status == InvestigationStatus.REJECTED
    assert inv.human_decision == {"decision": "reject", "reason": "duplicate"}


@pytest.mark.django_db
def test_stale_marks_promoted_investigation_stale(client, auth_headers, cluster):
    inv = _make_investigation(cluster, status=InvestigationStatus.PROMOTED)
    resp = client.post(
        f"/api/v1/investigations/{inv.id}/stale/",
        data={"stale_reason": StaleReason.PROMPT_CHANGED},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    inv.refresh_from_db()
    assert inv.status == InvestigationStatus.STALE
    assert inv.stale_reason == StaleReason.PROMPT_CHANGED


# ---------------------------------------------------------------------------
# Ideations endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ideations_list_and_detail(client, auth_headers, cluster):
    inv = _make_investigation(cluster, status=InvestigationStatus.PROMOTED)
    ideation = Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.AWAITING_REVIEW,
        guidance="try a smaller wedge",
        output={"schema_version": "1.0", "concepts": []},
    )

    list_resp = client.get("/api/v1/ideations/", **auth_headers)
    assert list_resp.status_code == 200
    assert str(ideation.id) in [r["id"] for r in list_resp.json()]

    detail_resp = client.get(f"/api/v1/ideations/{ideation.id}/", **auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["output"]["schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# Orchestrator-level (not via HTTP) — concurrency on promote
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_promote_orchestrator_rejects_concurrent_second_call(api_user, cluster, monkeypatch):
    """Two callers attempt to promote the same row.

    With ``select_for_update`` + the precondition recheck, exactly one
    transaction commits the PROMOTED flip; the other sees AWAITING_REVIEW
    has already been consumed and raises InvestigationNotInState.

    True concurrency would require threads (and a separate DB connection
    per thread). The deterministic version: call once, then again — the
    second call should hit the 409 path. The harder thread-based test is
    fragile under SQLite-style runners; we exercise the equivalent code
    path here.
    """
    _stub_noop_delay(monkeypatch, "agents.orchestrator.run_agent_loop")
    from investigations.orchestrator import (  # noqa: PLC0415  # tests bypass top-level coupling
        InvestigationNotInState,
        promote_investigation,
    )

    inv = _make_investigation(cluster)

    promote_investigation(investigation_id=inv.id, user=api_user)
    with pytest.raises(InvestigationNotInState):
        promote_investigation(investigation_id=inv.id, user=api_user)
