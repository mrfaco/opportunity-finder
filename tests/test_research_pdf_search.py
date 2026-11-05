"""Focused checks for readable PDFs and independently audited search failover."""

import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from django.test import override_settings
from django.utils import timezone

from discovery.agent_tools import AgentTools
from discovery.connectors.base import ConnectorError, ResearchDocument, ResearchPage
from discovery.connectors.pdf import extract_pdf
from discovery.connectors.web import WebFetchConnector, _PdfLinks, _status_page
from discovery.models import Discovery, DiscoveryTask, DiscoveryTurn
from discovery.research import perform_research, research_calls_used
from tests.discovery_fixtures import pdf_bytes as _pdf


@pytest.fixture
def two_pages():
    return _pdf(["First page", "Decisive annex 8471"])


def test_pdf_page_selection_and_visible_provenance(two_pages):
    result = extract_pdf(two_pages, "https://regulator.example/annex.pdf#page=2", "application/pdf")
    assert len(result.documents) == 1
    document = result.documents[0]
    assert "Decisive annex 8471" in document.text
    assert "First page" not in document.text
    assert document.url.endswith("#page=2")
    assert document.metadata["page_count"] == 2
    rendered = json.loads(AgentTools.model_result(result.model_dump(mode="json")))
    assert rendered["documents"][0]["metadata"]["page"] == 2


def test_pdf_limits_and_unreadable_documents(two_pages):
    with pytest.raises(ConnectorError, match="1–10"):
        extract_pdf(two_pages, "https://example.org/a.pdf#page=1-11", "application/pdf")
    with pytest.raises(ConnectorError, match="only 2"):
        extract_pdf(two_pages, "https://example.org/a.pdf#page=3", "application/pdf")
    blank = _pdf([""])
    result = extract_pdf(blank, "https://example.org/scan.pdf", "application/pdf")
    assert result.status == "error" and "OCR" in result.error_reason
    with patch(
        "discovery.connectors.web.fetch_public_url", side_effect=ConnectorError("PDF is corrupt")
    ):
        assert (
            "PDF is corrupt" in WebFetchConnector().fetch("https://example.org/a.pdf").error_reason
        )


def _task(max_calls=8):
    run = Discovery.objects.create(
        objective="test sources",
        request_hash=str(uuid4()),
        config_snapshot={
            "connectors": ["search_auto", "web_fetch"],
            "max_research_calls_per_turn": max_calls,
        },
    )
    task = DiscoveryTask.objects.create(
        discovery=run,
        role="finder",
        turn_number=1,
        state="working",
        lease_token=uuid4(),
        lease_expires_at=timezone.now() + timedelta(minutes=3),
    )
    DiscoveryTurn.objects.create(discovery=run, task=task, role="finder", number=1)
    return task


@pytest.mark.django_db
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "search-fallback-test",
        }
    }
)
def test_ddg_challenge_falls_back_to_bing_with_separate_ledger_and_idempotency():
    task = _task(max_calls=2)
    rss = "<rss><channel><item><title>Primary</title><link>https://regulator.example/</link><description>Evidence</description></item></channel></rss>"
    replies = [httpx.Response(202), httpx.Response(200, text=rss)]
    with patch("discovery.connectors.web.httpx.get", side_effect=replies) as request:
        result = perform_research(
            task, "fallback", {"connector": "search_auto", "action": "search", "query": "BCRA"}
        )
        replay = perform_research(
            task, "fallback", {"connector": "search_auto", "action": "search", "query": "BCRA"}
        )
    assert request.call_count == 2
    assert [x["status"] for x in result["search_attempts"]] == ["rate_limited", "success"]
    assert result["documents"][0]["connector"] == "bing"
    assert result == replay
    assert task.operations.count() == 2 and research_calls_used(task) == 2
    assert task.discovery.evidence.count() == 1


@pytest.mark.django_db
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "search-quota-test",
        }
    }
)
def test_fallback_cannot_exceed_research_quota():
    task = _task(max_calls=1)
    with patch("discovery.connectors.web.httpx.get", return_value=httpx.Response(202)) as request:
        result = perform_research(
            task, "one-slot", {"connector": "search_auto", "action": "search", "query": "x"}
        )
    assert request.call_count == 1
    assert result["fallback_not_attempted"] == "Research quota exhausted"
    assert research_calls_used(task) == 1


@pytest.mark.django_db
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "host-throttle-test",
        }
    }
)
def test_source_rate_limit_does_not_block_other_hosts():
    task = _task()
    page = ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                external_id="b",
                url="https://other.example/",
                text="Readable source",
                kind="fetched_page",
            )
        ]
    )
    with patch(
        "discovery.connectors.web.fetch_public_url", side_effect=[_status_page(429, "90"), page]
    ) as request:
        first = perform_research(
            task,
            "first",
            {
                "connector": "web_fetch",
                "action": "fetch",
                "target": "https://datospublicos.example/a",
            },
        )
        denied = perform_research(
            task,
            "denied",
            {
                "connector": "web_fetch",
                "action": "fetch",
                "target": "https://datospublicos.example/b",
            },
        )
        other = perform_research(
            task,
            "other",
            {"connector": "web_fetch", "action": "fetch", "target": "https://other.example/"},
        )
    assert first["status"] == "rate_limited" and first["retry_after_seconds"] == 90
    assert denied["request_performed"] is False
    assert other["status"] == "success" and request.call_count == 2
    assert research_calls_used(task) == 2


def test_fetch_pdf_transport_and_redirect_preserve_requested_page(two_pages):
    redirect = MagicMock(status=302)
    redirect.getheader.side_effect = lambda name: {
        "Location": "https://regulator.example/final.pdf"
    }.get(name)
    response = MagicMock(status=200)
    response.getheader.side_effect = lambda name: {"Content-Type": "application/pdf"}.get(name)
    response.read.side_effect = BytesIO(two_pages).read
    connection = MagicMock()
    connection.getresponse.side_effect = [redirect, response]
    with (
        patch(
            "discovery.connectors.web.public_url_address",
            return_value=("https", "regulator.example", 443, "93.184.216.34"),
        ),
        patch("discovery.connectors.web.http.client.HTTPSConnection", return_value=connection),
    ):
        result = WebFetchConnector().fetch("https://regulator.example/original#page=2")
    assert result.status == "success"
    assert result.documents[0].url == "https://regulator.example/final.pdf#page=2"
    assert "Decisive annex" in result.documents[0].text


def test_regulator_page_preserves_linked_pdf_targets():
    parser = _PdfLinks("https://regulator.example/rules/")
    parser.feed('<a href="../annex.pdf?rev=2">Annex</a><a href="javascript:bad.pdf">bad</a>')
    assert parser.links == ["https://regulator.example/annex.pdf?rev=2"]
