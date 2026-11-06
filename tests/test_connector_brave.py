"""brave: opt-in keyed search; preflight, mapping, parsing, estimated cost, typed failures."""

from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings

from discovery.connectors.base import ConnectorConfigurationError
from discovery.connectors.brave import BraveSearchConnector
from discovery.orchestrator import start_discovery
from discovery.research import perform_research
from discovery.schemas import DiscoveryConfig
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    provider_fixture,
    research_task,
)

KEYED = {"BRAVE_SEARCH_API_KEY": "brave-test-key"}
LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "brave"}
}


def _search(stub, *args, **kwargs):
    with stub.patch():
        return BraveSearchConnector().search(*args, **kwargs)


def test_unconfigured_brave_is_described_and_refused_without_a_request():
    description = BraveSearchConnector().describe()
    assert not description.configured
    assert description.configuration_error == "BRAVE_SEARCH_API_KEY is not configured"
    stub = ProviderStub()
    with pytest.raises(ConnectorConfigurationError, match="BRAVE_SEARCH_API_KEY"):
        _search(stub, "q")
    assert stub.requests == []


@pytest.mark.django_db
def test_start_discovery_preflight_names_the_missing_key():
    with (
        patch("discovery.runtime.check_configuration", return_value={"profiles": {}}),
        pytest.raises(ConnectorConfigurationError, match="BRAVE_SEARCH_API_KEY"),
    ):
        start_discovery("Find a services business", {"connectors": ["brave", "web_fetch"]})


def test_discovery_config_accepts_brave_as_general_search():
    assert DiscoveryConfig(connectors=["brave", "web_fetch"]).connectors == ["brave", "web_fetch"]
    with pytest.raises(ValueError, match="web_search, brave, duckduckgo, bing or search_auto"):
        DiscoveryConfig(connectors=["reddit", "web_fetch"])


@override_settings(**KEYED)
def test_market_and_period_filters_and_cursor_map_onto_the_request():
    stub = ProviderStub(json_reply({}), json_reply({}))
    _search(stub, "contador", {"country": "ar", "language": "es", "recency": "week"}, cursor="2")
    _search(stub, "contador", {"country": "pt", "language": "pt"}, limit=3)
    first, second = stub.requests
    assert first["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert first["params"] == {
        "q": "contador",
        "count": 5,
        "offset": 2,
        "country": "AR",
        "search_lang": "es",
        "freshness": "pw",
    }
    assert first["headers"]["X-Subscription-Token"] == "brave-test-key"
    assert second["params"]["search_lang"] == "pt-pt" and second["params"]["count"] == 3
    with pytest.raises(ValueError, match="cursor must be"):
        _search(ProviderStub(), "q", cursor="10")


@override_settings(**KEYED, DISCOVERY_BRAVE_SEARCH_COST_USD="0.005")
def test_results_are_snippets_with_stated_dates_and_estimated_cost():
    page = _search(ProviderStub(json_reply(provider_fixture("brave_web"))), "q")
    assert [d.url for d in page.documents] == [
        "https://www.consejo.org.ar/honorarios-minimos-sugeridos",
        "https://www.contaexpress.com/precios",
    ]
    first, second = page.documents
    assert first.kind == "search_snippet"
    assert first.title == "Honorarios contador monotributo 2026"
    assert "<strong>" not in first.text and "monotributo" in first.text
    assert first.published_at == "2026-10-30T14:00:00+00:00"
    assert second.published_at is None
    assert page.next_cursor == "1" and page.truncated
    assert page.cost_usd == Decimal("0.005") and page.cost_is_estimate
    assert "brave-test-key" not in BraveSearchConnector().describe().model_dump_json()


@override_settings(**KEYED)
def test_bad_key_and_other_failures_are_typed():
    page = _search(ProviderStub(httpx.Response(401)), "q")
    assert page.status == "access_denied" and page.error_reason.startswith("Brave Search")
    assert page.cost_usd == 0
    assert_typed_failures(lambda: BraveSearchConnector().search("q"))
    assert _search(ProviderStub(json_reply([1])), "q").status == "error"


@pytest.mark.django_db
@override_settings(**KEYED, DISCOVERY_BRAVE_SEARCH_COST_USD="0.005", CACHES=LOCMEM)
def test_research_ledger_records_the_brave_cost_as_an_estimate():
    task = research_task(connectors=["brave", "web_fetch"])
    with ProviderStub(json_reply(provider_fixture("brave_web"))).patch():
        result = perform_research(
            task, "brave-1", {"connector": "brave", "action": "search", "query": "contador"}
        )
    operation = task.operations.get(operation_key="brave-1")
    assert operation.reserved_usd == Decimal("0.005")
    assert operation.cost_usd == Decimal("0.005")
    assert operation.response["cost_is_estimate"] is True
    assert result["cost_usd"] == "0.005"
    task.discovery.refresh_from_db()
    assert task.discovery.cost_used_usd == Decimal("0.005")
