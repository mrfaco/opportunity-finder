"""Tests for the ideation agent: schema, orchestrator, loop branch, admin wiring."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.urls import reverse
from django.utils import timezone

from agents import loop as agent_loop
from agents import prompts as prompt_loader
from agents import tools as tool_registry
from agents.models import (
    AgentRun,
    AgentRunStatus,
    AgentRunTrigger,
    TerminationReason,
)
from agents.tools import all_tools_for_agent
from clusters.models import (
    EMBEDDING_DIM,
    Cluster,
    ClusterStatus,
    Source,
)
from ideation import orchestrator as ideation_orchestrator
from ideation.models import Ideation, IdeationStatus
from ideation.schemas import IdeationOutput
from investigations.models import Investigation, InvestigationStatus


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _concept(name: str, bet_axis: str) -> dict:
    return {
        "schema_version": "1.0",
        "name": name,
        "bet_axis": bet_axis,
        "one_liner": f"{name}: a thing for someone",
        "core_features": ["feature one", "feature two", "feature three"],
        "explicitly_not_included": ["custom auth", "white-label"],
        "buyer": "Solo agency operator running 3-10 agents in production",
        "rough_pricing_hypothesis": "$49-99/mo per team",
        "competitive_landscape": [
            {
                "schema_version": "1.0",
                "name": "Existing thing",
                "url": "https://existing.example",
                "positioning": "Targets devs not operators",
                "overlap": "Partial — observability only",
                "threat_level": "low",
                "evidence": "Pricing page targets engineering teams",
            }
        ],
        "mvp_scope": {
            "schema_version": "1.0",
            "build_size": "S",
            "build_estimate_assumptions": "Solo full-stack, no orchestration internals",
            "minimum_features_for_test": ["approval queue", "single agent integration"],
            "explicitly_deferred_to_v2": ["multi-tenant", "permissions"],
        },
        "first_validation_test": (
            "Ship the approval-queue MVP to the HN poster in 2 weeks, ask if "
            "they'd pay $49/mo as-is."
        ),
        "kill_criteria": [
            "Original poster declines at $19/mo",
            "Three existing tools doing exactly this surface in first 4 hours",
        ],
        "fit_to_builder": {
            "schema_version": "1.0",
            "distribution_fit": "HN + niche subreddits reach this buyer",
            "skill_fit": "Solo full-stack build, no ML infra",
            "capital_fit": "$0 marketing, <$50/mo hosting",
        },
    }


def _valid_ideation_output(investigation_id: str, guidance: str = "") -> dict:
    return {
        "schema_version": "1.0",
        "investigation_id": investigation_id,
        "guidance": guidance,
        "generated_at": timezone.now().isoformat(),
        "concepts": [
            _concept("Alpha", "minimal_scope"),
            _concept("Bravo", "aggressive_scope"),
            _concept("Charlie", "different_buyer"),
        ],
        "ideation_notes": "All three assume the operator segment is real.",
    }


def _make_cluster() -> Cluster:
    now = timezone.now()
    return Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec(),
        title="Indie agent tooling",
        summary="Operators want a control plane for their agents",
        category_tags=["dev-tools"],
        classifier_score=0.9,
    )


def _make_investigation(cluster: Cluster) -> Investigation:
    """Create an Investigation with a minimal but realistic brief."""
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
        status=InvestigationStatus.PROMOTED,
        brief={
            "schema_version": "1.0",
            "headline": "Operator GUI for AI-agent power users",
            "problem_statement": "Non-devs running agents have no operator UI.",
            "target_user": "Marketing agencies running 3-10 agents",
            "evidence_summary": "Multiple HN comments echo the same pain.",
            "evidence": [],
            "competitors": [],
            "differentiators": ["operator UX vs developer SDK"],
            "risks": ["niche may be too small"],
            "confidence": 0.7,
            "recommended_next_step": "Talk to five marketing agency founders.",
        },
        cluster_snapshot={"id": str(cluster.id)},
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
def test_ideation_output_requires_exactly_three_concepts():
    payload = _valid_ideation_output("11111111-1111-1111-1111-111111111111")
    payload["concepts"] = payload["concepts"][:2]
    with pytest.raises(ValueError, match="exactly 3 concepts"):
        IdeationOutput.model_validate(payload)


def test_ideation_output_requires_three_distinct_axes():
    payload = _valid_ideation_output("22222222-2222-2222-2222-222222222222")
    # Two concepts share an axis.
    payload["concepts"][1]["bet_axis"] = payload["concepts"][0]["bet_axis"]
    with pytest.raises(ValueError, match="distinct bet_axis"):
        IdeationOutput.model_validate(payload)


def test_ideation_output_valid_payload_round_trips():
    payload = _valid_ideation_output("33333333-3333-3333-3333-333333333333")
    parsed = IdeationOutput.model_validate(payload)
    assert len(parsed.concepts) == 3
    assert {c.bet_axis for c in parsed.concepts} == {
        "minimal_scope",
        "aggressive_scope",
        "different_buyer",
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
def test_record_ideation_tool_is_registered():
    tool = tool_registry.get_tool("record_ideation")
    assert tool.input_type is IdeationOutput
    assert tool.cost_tier == 0


def test_ideation_agent_toolset_includes_record_ideation():
    names = [t.name for t in all_tools_for_agent("ideation")]
    assert "record_ideation" in names
    assert "query_cluster" in names
    # Unimplemented stubs are excluded by AGENT_TOOLSETS.
    assert "query_known_competitors" not in names
    assert "query_trustmrr" not in names


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------
def test_ideation_prompts_load_with_frontmatter():
    prompts = prompt_loader.get_prompts_for_agent("ideation")
    assert "system" in prompts
    assert "procedural" in prompts
    for p in prompts.values():
        assert p.content
        assert p.hash


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_start_ideation_creates_draft_row_and_run(monkeypatch, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    # Stub Celery task at the call site (agents.orchestrator imported it
    # into its own namespace at module load).
    from agents import orchestrator as agents_orchestrator

    class _NoOpTask:
        def delay(self, *_a, **_kw):
            return None

    monkeypatch.setattr(agents_orchestrator, "run_agent_loop", _NoOpTask())

    cluster = _make_cluster()
    inv = _make_investigation(cluster)

    ideation_id, run_id = ideation_orchestrator.start_ideation(
        investigation_id=inv.id, guidance="try a smaller wedge"
    )

    ideation = Ideation.objects.get(pk=ideation_id)
    assert ideation.status == IdeationStatus.DRAFT
    assert ideation.guidance == "try a smaller wedge"
    assert ideation.investigation_id == inv.id
    assert ideation.primary_run_id == run_id

    run = AgentRun.objects.get(pk=run_id)
    assert run.agent_name == "ideation"
    assert run.cluster_id == cluster.id
    # The investigation snapshot lives under the ideation_input key.
    ideation_input = run.config_snapshot["ideation_input"]
    assert ideation_input["ideation_id"] == str(ideation_id)
    assert ideation_input["investigation"]["id"] == str(inv.id)
    assert ideation_input["guidance"] == "try a smaller wedge"


# ---------------------------------------------------------------------------
# Loop branch — initial history and terminal-tool persistence
# ---------------------------------------------------------------------------
def _tool_use_block(tool_id: str, name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _make_ideation_run(
    cluster: Cluster, investigation: Investigation, ideation: Ideation, guidance: str = ""
) -> AgentRun:
    prompts = prompt_loader.get_prompts_for_agent("ideation")
    return AgentRun.objects.create(
        agent_name="ideation",
        cluster=cluster,
        trigger=AgentRunTrigger.MANUAL,
        status=AgentRunStatus.RUNNING,
        budget_max_steps=10,
        budget_max_cost_usd=Decimal("0.50"),
        budget_max_duration_s=300,
        models_used=["claude-sonnet-4-6"],
        config_snapshot={
            "prompts": {
                k: {"content": p.content, "hash": p.hash, "path": p.path}
                for k, p in prompts.items()
            },
            "tool_registry_version": "tooldigest",
            "tool_names": [t.name for t in all_tools_for_agent("ideation")],
            "model_selection": {"ideation": "claude-sonnet-4-6"},
            "budgets": {
                "max_steps": 10,
                "max_cost_usd": "0.50",
                "max_duration_s": 300,
            },
            "ideation_input": {
                "ideation_id": str(ideation.id),
                "investigation": {
                    "id": str(investigation.id),
                    "brief": investigation.brief,
                    "brief_schema_version": investigation.brief_schema_version,
                    "cluster_snapshot": investigation.cluster_snapshot,
                    "cluster_id": str(cluster.id),
                },
                "guidance": guidance,
            },
        },
        cluster_snapshot={
            "id": str(cluster.id),
            "status": cluster.status,
            "title": cluster.title,
            "summary": cluster.summary,
            "size": cluster.size,
            "sources": list(cluster.sources),
            "category_tags": list(cluster.category_tags),
            "first_seen_at": cluster.first_seen_at.isoformat(),
            "last_seen_at": cluster.last_seen_at.isoformat(),
            "classifier_score": cluster.classifier_score,
            "item_ids": [],
        },
        prompt_hashes={k: p.hash for k, p in prompts.items()},
    )


@pytest.mark.django_db
def test_ideation_initial_history_includes_brief_and_guidance():
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    ideation = Ideation.objects.create(investigation=inv, guidance="try a smaller wedge")
    run = _make_ideation_run(cluster, inv, ideation, guidance="try a smaller wedge")

    history = agent_loop._build_initial_history(run)

    user_turn = history[-1]
    assert user_turn["role"] == "user"
    assert "Ideate on investigation" in user_turn["content"]
    assert str(inv.id) in user_turn["content"]
    assert "try a smaller wedge" in user_turn["content"]
    assert inv.brief["headline"] in user_turn["content"]


@pytest.mark.django_db
def test_ideation_loop_records_output_and_flips_status(monkeypatch):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    ideation = Ideation.objects.create(investigation=inv)
    run = _make_ideation_run(cluster, inv, ideation)

    output_payload = _valid_ideation_output(str(inv.id))

    def fake_call_model(_history, _model, _tools):
        return {
            "content": [_tool_use_block("tu_1", "record_ideation", output_payload)],
            "stop_reason": "tool_use",
            "input_tokens": 250,
            "output_tokens": 400,
            "cached_tokens": 150,
            "tool_calls": [{"id": "tu_1", "name": "record_ideation", "input": output_payload}],
            "final_text": None,
        }

    monkeypatch.setattr(agent_loop, "_call_model", fake_call_model)

    agent_loop.run_loop(run.id)

    run.refresh_from_db()
    assert run.status == AgentRunStatus.COMPLETED
    assert run.termination_reason == TerminationReason.AGENT_DECIDED_DONE
    assert run.final_output["concepts"][0]["name"] == "Alpha"

    ideation.refresh_from_db()
    assert ideation.status == IdeationStatus.AWAITING_REVIEW
    assert ideation.output["investigation_id"] == str(inv.id)
    assert len(ideation.output["concepts"]) == 3
    assert ideation.primary_run_id == run.id


# ---------------------------------------------------------------------------
# Admin wiring — promote action enqueues ideation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_promote_action_enqueues_ideation(monkeypatch, admin_user, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    from agents import orchestrator as agents_orchestrator

    class _NoOpTask:
        def delay(self, *_a, **_kw):
            return None

    monkeypatch.setattr(agents_orchestrator, "run_agent_loop", _NoOpTask())

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    inv.status = InvestigationStatus.AWAITING_REVIEW
    inv.save(update_fields=["status"])

    # Drive the admin action directly.
    from django.contrib.admin.sites import AdminSite

    from investigations.admin import InvestigationAdmin

    site = AdminSite()
    admin = InvestigationAdmin(Investigation, site)

    request = MagicMock()
    request.user = admin_user

    admin.promote(request, Investigation.objects.filter(pk=inv.id))

    inv.refresh_from_db()
    assert inv.status == InvestigationStatus.PROMOTED
    # An ideation row was created in draft for this investigation.
    assert Ideation.objects.filter(investigation=inv, status=IdeationStatus.DRAFT).exists()


@pytest.mark.django_db
def test_promote_from_latest_view_promotes_and_enqueues_ideation(
    monkeypatch, admin_user, client, settings
):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    from agents import orchestrator as agents_orchestrator

    class _NoOpTask:
        def delay(self, *_a, **_kw):
            return None

    monkeypatch.setattr(agents_orchestrator, "run_agent_loop", _NoOpTask())

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    inv.status = InvestigationStatus.AWAITING_REVIEW
    inv.save(update_fields=["status"])

    client.force_login(admin_user)
    resp = client.post(f"/admin/investigations/{inv.id}/promote/")

    assert resp.status_code == 302
    assert resp.url == "/admin/investigations/latest/"

    inv.refresh_from_db()
    assert inv.status == InvestigationStatus.PROMOTED
    assert Ideation.objects.filter(investigation=inv, status=IdeationStatus.DRAFT).exists()


@pytest.mark.django_db
def test_promote_from_latest_view_rejects_get(admin_user, client):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    client.force_login(admin_user)
    resp = client.get(f"/admin/investigations/{inv.id}/promote/")
    assert resp.status_code == 405  # method not allowed


@pytest.mark.django_db
def test_promote_from_latest_view_noops_when_not_awaiting_review(
    monkeypatch, admin_user, client, settings
):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    from agents import orchestrator as agents_orchestrator

    class _NoOpTask:
        def delay(self, *_a, **_kw):
            return None

    monkeypatch.setattr(agents_orchestrator, "run_agent_loop", _NoOpTask())

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    # _make_investigation already sets status=PROMOTED — perfect setup for the no-op path.
    assert inv.status == InvestigationStatus.PROMOTED

    client.force_login(admin_user)
    resp = client.post(f"/admin/investigations/{inv.id}/promote/")

    assert resp.status_code == 302
    inv.refresh_from_db()
    # Still promoted, no new Ideation enqueued by this endpoint.
    assert inv.status == InvestigationStatus.PROMOTED
    assert not Ideation.objects.filter(investigation=inv).exists()


@pytest.mark.django_db
def test_re_ideate_view_creates_new_ideation_with_guidance(
    monkeypatch, admin_user, client, settings
):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    from agents import orchestrator as agents_orchestrator

    class _NoOpTask:
        def delay(self, *_a, **_kw):
            return None

    monkeypatch.setattr(agents_orchestrator, "run_agent_loop", _NoOpTask())

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    client.force_login(admin_user)

    url = reverse("admin:ideation_ideation_re_ideate", args=[str(inv.id)])
    resp = client.post(url, data={"guidance": "consider open-source play"})

    assert resp.status_code == 302  # redirects to the new ideation's change page
    new_ideation = Ideation.objects.filter(
        investigation=inv, guidance="consider open-source play"
    ).first()
    assert new_ideation is not None
    assert new_ideation.status == IdeationStatus.DRAFT


# ---------------------------------------------------------------------------
# Accept / reject admin actions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_accept_action_flips_status_and_records_user(admin_user):
    from django.contrib.admin.sites import AdminSite

    from ideation.admin import IdeationAdmin

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    ideation = Ideation.objects.create(investigation=inv, status=IdeationStatus.AWAITING_REVIEW)

    site = AdminSite()
    admin = IdeationAdmin(Ideation, site)
    request = MagicMock()
    request.user = admin_user
    admin.accept(request, Ideation.objects.filter(pk=ideation.id))

    ideation.refresh_from_db()
    assert ideation.status == IdeationStatus.ACCEPTED
    assert ideation.decided_by_user_id == admin_user.id
    assert ideation.decided_at is not None


@pytest.mark.django_db
def test_reject_action_flips_status(admin_user):
    from django.contrib.admin.sites import AdminSite

    from ideation.admin import IdeationAdmin

    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    ideation = Ideation.objects.create(investigation=inv, status=IdeationStatus.AWAITING_REVIEW)

    site = AdminSite()
    admin = IdeationAdmin(Ideation, site)
    request = MagicMock()
    request.user = admin_user
    admin.reject(request, Ideation.objects.filter(pk=ideation.id))

    ideation.refresh_from_db()
    assert ideation.status == IdeationStatus.REJECTED


@pytest.mark.django_db
def test_changelist_defaults_to_awaiting_review(admin_user, client):
    client.force_login(admin_user)
    resp = client.get(reverse("admin:ideation_ideation_changelist"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_change_view_renders_concepts(admin_user, client):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    output = _valid_ideation_output(str(inv.id))
    ideation = Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.AWAITING_REVIEW,
        output=output,
    )

    client.force_login(admin_user)
    resp = client.get(reverse("admin:ideation_ideation_change", args=[str(ideation.id)]))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Alpha" in content
    assert "Bravo" in content
    assert "Charlie" in content
    assert "minimal_scope" in content


@pytest.mark.django_db
def test_guidance_preview_truncates_long_strings():
    from django.contrib.admin.sites import AdminSite

    from ideation.admin import IdeationAdmin

    site = AdminSite()
    admin = IdeationAdmin(Ideation, site)

    short = Ideation(guidance="short note")
    long_text = "x" * 200
    long_ideation = Ideation(guidance=long_text)
    empty = Ideation(guidance="")

    assert admin.guidance_preview(short) == "short note"
    truncated = admin.guidance_preview(long_ideation)
    assert truncated.endswith("…")
    assert len(truncated) == 81
    assert admin.guidance_preview(empty) == "—"


@pytest.mark.django_db
def test_re_ideate_view_get_renders_form(admin_user, client):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    client.force_login(admin_user)
    url = reverse("admin:ideation_ideation_re_ideate", args=[str(inv.id)])
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Re-ideate investigation" in resp.content
    assert b'name="guidance"' in resp.content


# ---------------------------------------------------------------------------
# Latest ideations dashboard
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_latest_ideations_view_renders_empty(admin_user, client):
    client.force_login(admin_user)
    resp = client.get("/admin/ideation/latest/")
    assert resp.status_code == 200
    assert b"Latest ideations" in resp.content
    assert b"No ideations yet" in resp.content


@pytest.mark.django_db
def test_latest_ideations_view_renders_rows_with_concepts(admin_user, client):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    output = _valid_ideation_output(str(inv.id))
    Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.AWAITING_REVIEW,
        output=output,
        guidance="try a smaller wedge",
    )

    client.force_login(admin_user)
    resp = client.get("/admin/ideation/latest/")
    assert resp.status_code == 200
    content = resp.content.decode()
    # Investigation headline, concept names, guidance, and detail link all surface.
    assert "Operator GUI for AI-agent power users" in content
    assert "Alpha" in content and "Bravo" in content and "Charlie" in content
    assert "try a smaller wedge" in content
    assert "/admin/ideation/ideation/" in content


@pytest.mark.django_db
def test_latest_ideations_view_filters_by_status(admin_user, client):
    cluster = _make_cluster()
    inv = _make_investigation(cluster)
    Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.ACCEPTED,
        output=_valid_ideation_output(str(inv.id)),
    )
    Ideation.objects.create(
        investigation=inv,
        status=IdeationStatus.REJECTED,
        output=_valid_ideation_output(str(inv.id)),
    )

    client.force_login(admin_user)
    resp = client.get("/admin/ideation/latest/?status=accepted")
    assert resp.status_code == 200
    # The accepted row exists, the rejected one is filtered out — both share
    # the same concept names so we use the status column instead.
    content = resp.content.decode()
    assert content.count("<td>accepted</td>") == 1
    assert "<td>rejected</td>" not in content


# ---------------------------------------------------------------------------
# Sanity: registered tool count includes record_ideation
# ---------------------------------------------------------------------------
def test_export_mcp_definitions_for_ideation_includes_record_ideation():
    defs = tool_registry.export_mcp_definitions("ideation")
    names = [d["name"] for d in defs]
    assert "record_ideation" in names
    for d in defs:
        json.dumps(d["inputSchema"])  # round-trip cleanly
