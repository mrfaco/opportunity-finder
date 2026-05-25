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
def test_task_runs_filters_by_prefix(client, auth_headers):
    from django_celery_results.models import TaskResult  # noqa: PLC0415

    TaskResult.objects.create(
        task_id="t1",
        task_name="ingestion.tasks.ingest_source",
        status="SUCCESS",
        task_args="('hacker_news',)",
    )
    TaskResult.objects.create(
        task_id="t2",
        task_name="clusters.tasks.refine_clusters_nightly",
        status="SUCCESS",
    )
    TaskResult.objects.create(
        task_id="t3",
        task_name="agents.tasks.run_agent_loop",
        status="SUCCESS",
    )
    # Prefix scoped to ingestion only — refinement + agent rows should not appear.
    resp = client.get("/api/v1/task-runs/?task_prefix=ingestion.tasks.", **auth_headers)
    assert resp.status_code == 200
    task_ids = [r["task_id"] for r in resp.json()]
    assert "t1" in task_ids
    assert "t2" not in task_ids
    assert "t3" not in task_ids
    # Unfiltered surfaces everything.
    resp = client.get("/api/v1/task-runs/", **auth_headers)
    task_ids = [r["task_id"] for r in resp.json()]
    assert {"t1", "t2", "t3"} <= set(task_ids)


# ---------------------------------------------------------------------------
# Refinement + cluster merge proposals
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refinement_runs_post_enqueues(client, auth_headers, monkeypatch):
    calls = _stub_noop_delay(monkeypatch, "clusters.tasks.refine_clusters_nightly")
    resp = client.post(
        "/api/v1/refinement/runs/",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert calls == [((), {})]


@pytest.fixture
def two_clusters_with_items(db):
    from clusters.models import ClusterStatus, Source  # noqa: PLC0415

    now = timezone.now()
    a = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=2,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        classifier_score=0.8,
        title="cluster A",
        summary="summary A",
    )
    b = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=2,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        classifier_score=0.7,
        title="cluster B",
        summary="summary B",
    )
    for c in (a, b):
        for i in range(2):
            ClusterItem.objects.create(
                cluster=c,
                source=Source.HACKER_NEWS,
                source_item_id=f"hn-{c.id}-{i}",
                url=f"https://example.com/{i}",
                title=f"Item {i}",
                posted_at=now,
                raw_text="x",
                snippet="x",
                classifier_verdict="opportunity",
                classifier_confidence=0.8,
                embedding=_vec(),
                added_to_cluster_at=now,
                assigned_at=now,
            )
    return a, b


def _make_merge_proposal(a, b, *, verdict: bool, confidence: float = 0.85):
    from clusters.models import ClusterMergeProposal, ProposalStatus  # noqa: PLC0415

    return ClusterMergeProposal.objects.create(
        cluster_a=a,
        cluster_b=b,
        centroid_similarity=0.91,
        status=ProposalStatus.PENDING_REVIEW,
        llm_judge_verdict=verdict,
        llm_judge_confidence=confidence,
        llm_judge_reasoning="judge said so",
    )


@pytest.mark.django_db
def test_merge_proposals_list_filters_by_judge_verdict(
    client, auth_headers, two_clusters_with_items
):
    a, b = two_clusters_with_items
    yes = _make_merge_proposal(a, b, verdict=True, confidence=0.92)
    # Make a second pair for the "no" proposal (can't reuse the same a+b
    # because nothing prevents two pending proposals for the same pair,
    # but it's cleaner to keep them distinct).
    no = _make_merge_proposal(b, a, verdict=False, confidence=0.71)

    resp = client.get("/api/v1/cluster-merge-proposals/?judge_verdict=true", **auth_headers)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert str(yes.id) in ids
    assert str(no.id) not in ids

    resp = client.get("/api/v1/cluster-merge-proposals/?judge_verdict=false", **auth_headers)
    ids = [r["id"] for r in resp.json()]
    assert str(no.id) in ids
    assert str(yes.id) not in ids


@pytest.mark.django_db
def test_merge_proposal_detail_surfaces_judge_reasoning(
    client, auth_headers, two_clusters_with_items
):
    a, b = two_clusters_with_items
    proposal = _make_merge_proposal(a, b, verdict=True)
    resp = client.get(f"/api/v1/cluster-merge-proposals/{proposal.id}/", **auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_judge_reasoning"] == "judge said so"
    assert body["cluster_a_title"] == "cluster A"
    assert body["cluster_b_summary"] == "summary B"


@pytest.mark.django_db
def test_merge_proposal_apply_flips_status_and_moves_items(
    client, auth_headers, two_clusters_with_items
):
    a, b = two_clusters_with_items
    proposal = _make_merge_proposal(a, b, verdict=True)

    resp = client.post(
        f"/api/v1/cluster-merge-proposals/{proposal.id}/apply/",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "applied"

    # b's items now belong to a, b's status flipped, proposal applied.
    from clusters.models import ClusterStatus  # noqa: PLC0415

    a.refresh_from_db()
    b.refresh_from_db()
    proposal.refresh_from_db()
    assert b.items.count() == 0
    assert a.items.count() == 4  # 2 original + 2 absorbed
    assert b.status == ClusterStatus.MERGED_INTO
    assert b.merged_into_cluster_id == a.id
    assert proposal.status == "applied"


@pytest.mark.django_db
def test_merge_proposal_apply_rejects_double_apply(client, auth_headers, two_clusters_with_items):
    a, b = two_clusters_with_items
    proposal = _make_merge_proposal(a, b, verdict=True)

    first = client.post(
        f"/api/v1/cluster-merge-proposals/{proposal.id}/apply/",
        content_type="application/json",
        **auth_headers,
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/cluster-merge-proposals/{proposal.id}/apply/",
        content_type="application/json",
        **auth_headers,
    )
    assert second.status_code == 409
    body = second.json()
    assert body["current_status"] == "applied"
    assert body["expected_status"] == "pending_review"


@pytest.mark.django_db
def test_merge_proposal_reject_records_review_notes(client, auth_headers, two_clusters_with_items):
    a, b = two_clusters_with_items
    proposal = _make_merge_proposal(a, b, verdict=False)

    resp = client.post(
        f"/api/v1/cluster-merge-proposals/{proposal.id}/reject/",
        data={"review_notes": "judge was right — different audiences"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    proposal.refresh_from_db()
    assert proposal.status == "rejected"
    assert "different audiences" in proposal.review_notes


@pytest.mark.django_db
def test_merge_proposal_apply_404(client, auth_headers):
    resp = client.post(
        "/api/v1/cluster-merge-proposals/11111111-1111-1111-1111-111111111111/apply/",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 404


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
def test_investigation_pdf_returns_application_pdf(client, auth_headers, cluster, monkeypatch):
    """The PDF view delegates to ``core.pdf.render_pdf``. We stub that so
    the test doesn't depend on WeasyPrint's system libs (Pango/Cairo).
    The bytes returned are passed straight through to the response.
    """
    inv = _make_investigation(cluster)

    captured = {}

    def _fake_render(template_name, context):
        captured["template"] = template_name
        captured["context"] = context
        return b"%PDF-fake-bytes"

    monkeypatch.setattr("api.views.render_pdf", _fake_render)
    resp = client.get(f"/api/v1/investigations/{inv.id}/pdf/", **auth_headers)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content == b"%PDF-fake-bytes"
    assert "investigation-" in resp["Content-Disposition"]
    assert captured["template"] == "pdf/investigation.html"
    # The context should include the investigation, its brief, and the cluster.
    assert captured["context"]["investigation"].id == inv.id
    assert captured["context"]["brief"]["headline"] == "An opportunity"
    assert captured["context"]["cluster"].id == cluster.id


@pytest.mark.django_db
def test_investigation_pdf_404(client, auth_headers):
    resp = client.get(
        "/api/v1/investigations/11111111-1111-1111-1111-111111111111/pdf/",
        **auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_ideation_pdf_returns_application_pdf(client, auth_headers, cluster, monkeypatch):
    inv = _make_investigation(cluster, status=InvestigationStatus.PROMOTED)
    ideation = Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.AWAITING_REVIEW,
        guidance="test guidance",
        output={"schema_version": "1.0", "concepts": []},
    )

    captured = {}

    def _fake_render(template_name, context):
        captured["template"] = template_name
        captured["context"] = context
        return b"%PDF-ideation-bytes"

    monkeypatch.setattr("api.views.render_pdf", _fake_render)
    resp = client.get(f"/api/v1/ideations/{ideation.id}/pdf/", **auth_headers)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content == b"%PDF-ideation-bytes"
    assert "ideation-" in resp["Content-Disposition"]
    assert captured["template"] == "pdf/ideation.html"
    assert captured["context"]["ideation"].id == ideation.id
    # Investigation headline is surfaced so the PDF can title itself.
    assert captured["context"]["investigation_headline"] == "An opportunity"


@pytest.mark.django_db
def test_ideation_pdf_404(client, auth_headers):
    resp = client.get(
        "/api/v1/ideations/22222222-2222-2222-2222-222222222222/pdf/",
        **auth_headers,
    )
    assert resp.status_code == 404


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
