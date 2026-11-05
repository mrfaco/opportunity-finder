"""Append-only human verdicts recorded through admin and API."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from pydantic import ValidationError

from api.models import ApiKey
from discovery.models import (
    Discovery,
    DiscoveryTask,
    DiscoveryTurn,
    DiscoveryVerdict,
    JudgmentRevision,
    ProposalRevision,
    ValidationRevision,
)
from discovery.verdicts import VerdictNotAllowed, record_verdict

CONTENT = {"title": "Bookkeeping retainer", "summary": "Monthly service"}


def _discovery(status: str) -> Discovery:
    return Discovery.objects.create(
        objective="Find a service", request_hash="test", status=status, ended_at=timezone.now()
    )


def _turn(discovery: Discovery, number: int, role: str) -> DiscoveryTurn:
    task = DiscoveryTask.objects.create(
        discovery=discovery, role=role, turn_number=number, state="done"
    )
    return DiscoveryTurn.objects.create(
        discovery=discovery,
        task=task,
        number=number,
        role=role,
        submission={"summary": role},
        completed_at=timezone.now(),
    )


@pytest.fixture
def api_headers(db, django_user_model):
    user = django_user_model.objects.create_user(username="apiuser", password="pass")
    _, raw = ApiKey.create_for_user(user=user, label="test")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def _api_post(client, headers, discovery, body):
    return client.post(
        f"/api/v1/discoveries/{discovery.pk}/verdict/",
        data=body,
        content_type="application/json",
        **headers,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_discoveries_accept_verdicts_and_regenerate_report(status):
    discovery = _discovery(status)
    assert discovery.report_markdown == ""
    row = record_verdict(discovery, "untested", "", user=None, via="api")
    assert row.schema_version == "1.0"
    assert row.proposal is None and row.judgment is None
    discovery.refresh_from_db()
    assert discovery.report_markdown.startswith("# Opportunity discovery report")
    assert discovery.report_json["discovery_id"] == str(discovery.pk)


@pytest.mark.django_db
def test_verdict_captures_final_proposal_and_its_current_judgment():
    discovery = _discovery("completed")
    first = ProposalRevision.objects.create(
        discovery=discovery, turn=_turn(discovery, 1, "finder"), content=CONTENT, content_hash="1"
    )
    final = ProposalRevision.objects.create(
        discovery=discovery, turn=_turn(discovery, 2, "finder"), content=CONTENT, content_hash="2"
    )
    review = ValidationRevision.objects.create(
        discovery=discovery,
        turn=_turn(discovery, 3, "validator"),
        proposal=final,
        verdict="accept",
        content={},
        content_hash="3",
    )
    judgment = JudgmentRevision.objects.create(
        discovery=discovery,
        turn=_turn(discovery, 4, "judge"),
        proposal=final,
        validation=review,
        verdict="approve",
        content={"summary": "ok"},
        content_hash="4",
    )
    row = record_verdict(discovery, "good", "", user=None, via="api")
    assert row.proposal_id == final.pk != first.pk
    assert row.judgment_id == judgment.pk


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_terminal_discoveries_refuse_verdicts(client, admin_user, api_headers, status):
    discovery = _discovery(status)
    with pytest.raises(VerdictNotAllowed, match=status):
        record_verdict(discovery, "good", "", user=None, via="api")
    response = _api_post(client, api_headers, discovery, {"verdict": "good"})
    assert response.status_code == 409
    assert "completed, failed or cancelled" in response.json()["detail"]
    client.force_login(admin_user)
    response = client.post(
        reverse("admin:discovery-verdict", args=[discovery.pk]), {"verdict": "good"}
    )
    assert response.status_code == 302
    page = client.get(reverse("admin:discovery-review", args=[discovery.pk]))
    assert "completed, failed or cancelled" in page.content.decode()
    assert (
        'action="/admin/discovery/discovery/'
        not in page.content.decode().split("Human verdict")[1].split("</div>")[0]
    )
    assert not DiscoveryVerdict.objects.exists()


@pytest.mark.django_db
def test_wrong_requires_notes_everywhere(client, admin_user, api_headers):
    discovery = _discovery("completed")
    with pytest.raises(ValidationError, match="wrong"):
        record_verdict(discovery, "wrong", "   ", user=None, via="api")
    response = _api_post(client, api_headers, discovery, {"verdict": "wrong", "notes": ""})
    assert response.status_code == 400
    response = _api_post(client, api_headers, discovery, {"verdict": "great"})
    assert response.status_code == 400
    response = _api_post(client, api_headers, discovery, {"verdict": "good", "score": 3})
    assert response.status_code == 400
    client.force_login(admin_user)
    client.post(reverse("admin:discovery-verdict", args=[discovery.pk]), {"verdict": "wrong"})
    assert not DiscoveryVerdict.objects.exists()


@pytest.mark.django_db
def test_two_verdicts_keep_history_and_api_reports_latest(client, admin_user, api_headers):
    discovery = _discovery("completed")
    response = _api_post(
        client,
        api_headers,
        discovery,
        {"schema_version": "1.0", "verdict": "weak", "notes": "Thin demand"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["verdict"] == "weak" and body["recorded_via"] == "api"
    assert body["recorded_by"] is None
    client.force_login(admin_user)
    response = client.post(
        reverse("admin:discovery-verdict", args=[discovery.pk]),
        {"verdict": "good", "notes": "Re-read with fresh eyes"},
    )
    assert response.status_code == 302
    assert DiscoveryVerdict.objects.filter(discovery=discovery).count() == 2
    latest = DiscoveryVerdict.objects.get(verdict="good")
    assert latest.recorded_by == admin_user and latest.recorded_via == "admin"
    detail = client.get(f"/api/v1/discoveries/{discovery.pk}/", **api_headers).json()
    assert detail["schema_version"] == "1.2"
    assert detail["verdict_count"] == 2
    assert detail["human_verdict"]["verdict"] == "good"
    page = client.get(reverse("admin:discovery-review", args=[discovery.pk])).content.decode()
    assert "Good</strong> (current)" in page
    assert "Thin demand" in page and "Re-read with fresh eyes" in page


@pytest.mark.django_db
def test_detail_without_verdict_reports_null(client, api_headers):
    discovery = _discovery("running")
    detail = client.get(f"/api/v1/discoveries/{discovery.pk}/", **api_headers).json()
    assert detail["human_verdict"] is None and detail["verdict_count"] == 0


@pytest.mark.django_db
def test_admin_verdict_requires_change_permission_and_post(client, django_user_model, admin_user):
    discovery = _discovery("completed")
    viewer = django_user_model.objects.create_user(username="viewer", password="pass")
    viewer.is_staff = True
    viewer.save()
    viewer.user_permissions.add(Permission.objects.get(codename="view_discovery"))
    client.force_login(viewer)
    url = reverse("admin:discovery-verdict", args=[discovery.pk])
    assert client.post(url, {"verdict": "good"}).status_code == 403
    page = client.get(reverse("admin:discovery-review", args=[discovery.pk])).content.decode()
    assert "Record verdict" not in page
    client.force_login(admin_user)
    assert client.get(url).status_code == 405
    page = client.get(reverse("admin:discovery-review", args=[discovery.pk])).content.decode()
    assert "Record verdict" in page
    assert not DiscoveryVerdict.objects.exists()
