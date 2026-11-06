"""wayback: CDX lookup, absence note, and snapshots typed and counted as the archived site."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from discovery.connectors.base import ResearchDocument, ResearchPage
from discovery.connectors.wayback import WaybackConnector
from discovery.connectors.web import _status_page
from discovery.research import perform_research
from discovery.termination import evidence_gaps
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    proposal,
    provider_fixture,
    research_task,
)

LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "wayback"}
}
SNAPSHOT = "https://web.archive.org/web/20210602174530id_/https://www.contaexpress.com/precios"


def _search(stub, *args, **kwargs):
    with stub.patch():
        return WaybackConnector().search(*args, **kwargs)


def test_cdx_request_uses_monthly_collapse_year_filters_and_match_mode():
    stub = ProviderStub(json_reply([]), json_reply([]))
    _search(stub, "contaexpress.com", {"from_year": 2019, "to_year": "2024", "match": "prefix"})
    _search(stub, "https://www.contaexpress.com/precios", limit=3)
    assert stub.requests[0]["url"] == "https://web.archive.org/cdx/search/cdx"
    assert stub.requests[0]["params"] == {
        "url": "contaexpress.com",
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
        "limit": 5,
        "matchType": "prefix",
        "from": 2019,
        "to": 2024,
    }
    assert stub.requests[1]["params"]["url"] == "https://www.contaexpress.com/precios"
    assert stub.requests[1]["params"]["limit"] == 3


@pytest.mark.parametrize(
    ("query", "filters", "error"),
    [
        ("accounting software argentina", {}, "must be a URL or host"),
        ("ftp://contaexpress.com", {}, "must be a URL or host"),
        ("contaexpress.com", {"match": "domain"}, "match must be exact or prefix"),
        ("contaexpress.com", {"from_year": 1990}, "from_year must be a year"),
        ("contaexpress.com", {"to_year": "next"}, "to_year must be a year"),
        ("contaexpress.com", {"from_year": 2024, "to_year": 2020}, "must not be after"),
    ],
)
def test_invalid_lookups_are_refused_before_any_request(query, filters, error):
    stub = ProviderStub()
    with pytest.raises(ValueError, match=error):
        _search(stub, query, filters)
    assert stub.requests == []


def test_captures_are_records_labelled_with_the_original_page():
    page = _search(ProviderStub(json_reply(provider_fixture("cdx"))), "contaexpress.com/precios")
    assert len(page.documents) == 4 and not page.truncated
    first = page.documents[0]
    assert first.kind == "source_record"
    assert first.url == (
        "https://web.archive.org/web/20190314082211id_/https://www.contaexpress.com/precios"
    )
    assert first.published_at == "2019-03-14T08:22:11+00:00"
    assert first.metadata == {
        "original_url": "https://www.contaexpress.com/precios",
        "canonical_url": "https://www.contaexpress.com/precios",
        "original_source_origin": "www.contaexpress.com",
        "mimetype": "text/html",
        "statuscode": "200",
        "query": "contaexpress.com/precios",
    }
    assert "Fetch https://web.archive.org/web/20190314082211id_/" in first.text
    assert page.documents[3].published_at is None


def test_zero_captures_is_success_with_the_absence_note():
    page = _search(ProviderStub(json_reply([])), "never-archived.example")
    assert page.status == "success" and page.documents == []
    assert "absence from the archive is not absence from the web" in page.error_reason
    header_only = [["urlkey", "timestamp", "original"]]
    assert _search(ProviderStub(json_reply(header_only)), "x.example").status == "success"
    bad = _search(ProviderStub(json_reply({"captures": []})), "x.example")
    assert bad.status == "error"
    assert_typed_failures(lambda: WaybackConnector().search("x.example"))


def _archived_page(url=SNAPSHOT, published_at=None):
    return ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                connector_version="1.2",
                external_id=url,
                url=url,
                title="Precios ContaExpress",
                text="Plan Monotributo ARS 9.900 por mes",
                kind="fetched_page",
                published_at=published_at,
                metadata={"truncated": False, "content_type": "text/html"},
            )
        ]
    )


def test_fetch_requests_raw_snapshot_and_relabels_it_as_the_original():
    with patch("discovery.connectors.web.fetch_public_url", return_value=_archived_page()) as fetch:
        page = WaybackConnector().fetch(SNAPSHOT.replace("id_/", "/"))
    fetch.assert_called_once_with(SNAPSHOT)
    (document,) = page.documents
    assert document.connector == "wayback" and document.kind == "fetched_page"
    assert document.published_at == "2021-06-02T17:45:30+00:00"
    assert document.metadata["published_at_source"] == "wayback_capture"
    assert document.metadata["canonical_url"] == "https://www.contaexpress.com/precios"
    assert document.metadata["original_source_origin"] == "www.contaexpress.com"
    assert document.metadata["captured_at"] == "2021-06-02T17:45:30+00:00"
    stated = _archived_page(published_at="2020-01-01T00:00:00+00:00")
    with patch("discovery.connectors.web.fetch_public_url", return_value=stated):
        document = WaybackConnector().fetch(SNAPSHOT).documents[0]
    assert document.published_at == "2020-01-01T00:00:00+00:00"
    assert "published_at_source" not in document.metadata


def test_fetch_labels_the_capture_actually_served_and_passes_failures_through():
    nearest = SNAPSHOT.replace("20210602174530", "20210610000000")
    with patch("discovery.connectors.web.fetch_public_url", return_value=_archived_page(nearest)):
        document = WaybackConnector().fetch(SNAPSHOT).documents[0]
    assert document.metadata["captured_at"] == "2021-06-10T00:00:00+00:00"
    with patch("discovery.connectors.web.fetch_public_url", return_value=_status_page(429, "40")):
        page = WaybackConnector().fetch(SNAPSHOT)
    assert page.status == "rate_limited" and page.retry_after_seconds == 40
    with pytest.raises(ValueError, match="snapshot URL"):
        WaybackConnector().fetch("https://www.contaexpress.com/precios")


@pytest.mark.django_db
@override_settings(CACHES=LOCMEM)
def test_snapshot_evidence_has_the_original_origin_and_type_and_is_one_origin_with_live_page():
    task = research_task(connectors=["search_auto", "web_fetch", "wayback"])
    with patch("discovery.connectors.web.fetch_public_url", return_value=_archived_page()):
        perform_research(
            task, "wb-1", {"connector": "wayback", "action": "fetch", "target": SNAPSHOT}
        )
    live_url = "https://www.contaexpress.com/precios"
    live = ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                external_id=live_url,
                url=live_url,
                text="Plan Monotributo ARS 19.900 por mes",
                kind="fetched_page",
            )
        ]
    )
    with patch("discovery.connectors.web.fetch_public_url", return_value=live):
        perform_research(
            task, "live-1", {"connector": "web_fetch", "action": "fetch", "target": live_url}
        )
    archived = task.discovery.evidence.get(connector="wayback")
    assert archived.url == SNAPSHOT
    assert archived.origin == "www.contaexpress.com"
    assert archived.source_type == "vendor"
    assert archived.metadata["source_type_rule"] == "path_segment:precios"
    current = task.discovery.evidence.get(connector="web_fetch")
    cited = proposal(
        task.turn,
        {"demand_evidence_ids": [str(archived.pk)], "competitor_evidence_ids": [str(current.pk)]},
    )
    assert evidence_gaps(task.discovery, cited) == ["Two distinct source origins are required"]
