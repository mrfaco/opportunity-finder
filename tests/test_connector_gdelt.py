"""news_gdelt: market/period mapping onto the DOC API query, fixture parsing, typed failures."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from discovery.connectors.base import ResearchDocument, ResearchPage, UnsupportedCapabilityError
from discovery.connectors.gdelt import GdeltNewsConnector
from discovery.research import perform_research
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    provider_fixture,
    research_task,
)

LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "gdelt"}
}


def _search(stub, *args, **kwargs):
    with stub.patch():
        return GdeltNewsConnector().search(*args, **kwargs)


def test_market_and_period_filters_build_the_outgoing_query():
    stub = ProviderStub(*(json_reply({}) for _ in range(3)))
    _search(stub, "honorarios contador", {"country": "ar", "language": "es", "recency": "month"})
    _search(stub, "steuerberater", {"country": "de", "language": "de"}, limit=3)
    _search(stub, "gestoria", {"country": "es"})
    first, second, third = (request["params"] for request in stub.requests)
    assert stub.requests[0]["url"] == "https://api.gdeltproject.org/api/v2/doc/doc"
    assert first == {
        "query": "honorarios contador sourcecountry:AR sourcelang:spa",
        "mode": "artlist",
        "format": "json",
        "maxrecords": 5,
        "timespan": "1m",
    }
    assert second["query"] == "steuerberater sourcecountry:GM sourcelang:deu"
    assert second["timespan"] == "3m" and second["maxrecords"] == 3
    assert third["query"] == "gestoria sourcecountry:SP"


@pytest.mark.parametrize(
    ("filters", "error"),
    [
        ({"country": "jp"}, "country jp is not mapped"),
        ({"language": "ja"}, "language ja is not mapped"),
        ({"country": "AR"}, "country must be"),
        ({"recency": "decade"}, "recency must be one of"),
        ({"subreddit": "x"}, "unsupported filters"),
    ],
)
def test_unmapped_or_invalid_filters_are_refused_before_any_request(filters, error):
    stub = ProviderStub()
    with pytest.raises((ValueError, UnsupportedCapabilityError), match=error):
        _search(stub, "q", filters)
    assert stub.requests == []
    with pytest.raises(UnsupportedCapabilityError, match="pagination"):
        _search(stub, "q", cursor="2")


def test_articles_are_snippets_with_publisher_urls_and_seen_dates():
    page = _search(ProviderStub(json_reply(provider_fixture("gdelt_artlist"))), "q", limit=5)
    assert [d.url for d in page.documents] == [
        "https://www.lanacion.com.ar/economia/monotributo-recategorizacion-2026-nid01112026/",
        "https://www.infobae.com/economia/2026/10/28/estudios-contables-pymes-honorarios/",
        "https://www.cronista.com/finanzas/contadores-demanda/",
    ]
    first = page.documents[0]
    assert first.kind == "search_snippet"
    assert (
        first.title
        == first.text
        == ("Monotributo: cuánto cobran los contadores por la recategorización")
    )
    assert first.published_at == "2026-11-01T12:15:00+00:00"
    assert first.metadata == {
        "domain": "lanacion.com.ar",
        "sourcecountry": "Argentina",
        "language": "Spanish",
        "query": "q",
    }
    assert "socialimage" not in first.metadata
    assert page.documents[2].published_at is None
    assert not page.truncated


def test_empty_result_is_success_and_html_error_page_is_typed():
    page = _search(ProviderStub(json_reply({})), "rare query")
    assert page.status == "success" and page.documents == []
    page = _search(ProviderStub(json_reply({"articles": "nope"})), "q")
    assert page.status == "error" and "unexpected response shape" in page.error_reason
    assert_typed_failures(lambda: GdeltNewsConnector().search("q"))


def test_description_declares_the_gdelt_interval_and_limits():
    description = GdeltNewsConnector().describe()
    assert description.minimum_interval_seconds == 5.0
    assert description.capabilities == ["search"]
    assert set(description.supported_filters) == {"country", "language", "recency"}
    assert "web_fetch" in description.access_limitations


@pytest.mark.django_db
@override_settings(CACHES=LOCMEM)
def test_research_records_gdelt_articles_as_snippet_leads():
    task = research_task(connectors=["search_auto", "web_fetch", "news_gdelt"])
    with ProviderStub(json_reply(provider_fixture("gdelt_artlist"))).patch():
        result = perform_research(
            task,
            "gdelt-1",
            {
                "connector": "news_gdelt",
                "action": "search",
                "query": "contador monotributo",
                "filters": {"country": "ar", "language": "es", "recency": "month"},
            },
        )
    assert result["status"] == "success" and len(result["documents"]) == 3
    rows = task.discovery.evidence.all()
    assert {row.source_type for row in rows} == {"search_snippet"}
    assert {row.origin for row in rows} == {
        "www.lanacion.com.ar",
        "www.infobae.com",
        "www.cronista.com",
    }
    article = result["documents"][0]["url"]
    page = ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                external_id=article,
                url=article,
                text="Los contadores cobran entre 30.000 y 60.000 pesos.",
                kind="fetched_page",
            )
        ]
    )
    with patch("discovery.connectors.web.fetch_public_url", return_value=page):
        perform_research(
            task, "fetch-1", {"connector": "web_fetch", "action": "fetch", "target": article}
        )
    fetched = task.discovery.evidence.get(kind="fetched_page")
    assert fetched.source_type == "news" and fetched.metadata["source_type_rule"] == (
        "domain:lanacion.com.ar"
    )
