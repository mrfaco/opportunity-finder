"""Market and period search filters, and search_auto fallback pairs sharing one research slot."""

import json
from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings

from discovery.agent_tools import AgentTools
from discovery.connectors.base import UnsupportedCapabilityError
from discovery.connectors.search import AutoSearchConnector, BingConnector
from discovery.connectors.web import DuckDuckGoConnector
from discovery.loop import tools_for_role
from discovery.research import ResearchInput, perform_research, research_calls_used
from tests.discovery_fixtures import research_task

RSS = (
    "<rss><channel><item><title>Result</title><link>https://result.example/</link>"
    "<description>Snippet</description></item></channel></rss>"
)
NO_THROTTLE = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}


class Recorder:
    """Records outgoing search requests and answers DuckDuckGo with a challenge."""

    def __init__(self, ddg_status: int = 202) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.ddg_status = ddg_status

    def __call__(self, url, params=None, **kwargs):
        self.requests.append((url, dict(params or {})))
        if "duckduckgo" in url:
            return httpx.Response(self.ddg_status, text="<html></html>")
        return httpx.Response(200, text=RSS)


def test_duckduckgo_and_bing_mappings_on_the_outgoing_request():
    recorder = Recorder(ddg_status=200)
    with patch("discovery.connectors.web.httpx.get", side_effect=recorder):
        DuckDuckGoConnector().search("q", {"country": "ar", "language": "es", "recency": "month"})
        DuckDuckGoConnector().search("q", {"country": "br"})
        DuckDuckGoConnector().search("q", {"recency": "year"})
        BingConnector().search("q", {"country": "mx", "language": "es"})
    params = [request[1] for request in recorder.requests]
    assert params[0] == {"q": "q", "kl": "ar-es", "df": "m"}
    assert params[1] == {"q": "q", "kl": "br-pt"}
    assert params[2] == {"q": "q", "df": "y"}
    assert params[3] == {"q": "q", "format": "rss", "cc": "MX", "setlang": "es"}
    assert set(AutoSearchConnector().describe().supported_filters) == {
        "country",
        "language",
        "recency",
    }


@pytest.mark.parametrize(
    ("connector", "filters", "error"),
    [
        (DuckDuckGoConnector(), {"country": "AR"}, "country must be"),
        (DuckDuckGoConnector(), {"country": "arg"}, "country must be"),
        (DuckDuckGoConnector(), {"country": "ar", "language": "spa"}, "language must be"),
        (DuckDuckGoConnector(), {"recency": "decade"}, "recency must be one of"),
        (DuckDuckGoConnector(), {"language": "es"}, "needs a country"),
        (DuckDuckGoConnector(), {"country": "jp"}, "give a language filter"),
        (AutoSearchConnector(), {"language": "es"}, "needs a country"),
        (BingConnector(), {"recency": "day"}, "unsupported filters"),
    ],
)
def test_invalid_values_refused_before_any_request(connector, filters, error):
    with patch("discovery.connectors.web.httpx.get") as request:
        with pytest.raises((ValueError, UnsupportedCapabilityError), match=error):
            if isinstance(connector, AutoSearchConnector):
                connector.validate_search("q", filters, None, 5)
            else:
                connector.search("q", filters)
    request.assert_not_called()


@pytest.mark.django_db
@override_settings(CACHES=NO_THROTTLE)
def test_invalid_filter_is_a_validation_failed_tool_result():
    task = research_task()
    with (
        AgentTools(task, tools_for_role("finder")) as bridge,
        patch("discovery.connectors.web.httpx.get") as request,
    ):
        response = bridge.handle(
            {
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "research",
                    "arguments": {
                        "connector": "search_auto",
                        "action": "search",
                        "query": "q",
                        "filters": {"recency": "decade"},
                    },
                },
            }
        )
    result = json.loads(response["content"][0]["text"])
    assert result["status"] == "validation_failed" and "recency must be one of" in result["error"]
    request.assert_not_called()
    assert task.operations.count() == 0


@pytest.mark.django_db
@override_settings(CACHES=NO_THROTTLE)
def test_fallback_drops_recency_for_bing_with_an_audit_record():
    task = research_task()
    recorder = Recorder()
    with patch("discovery.connectors.web.httpx.get", side_effect=recorder):
        result = perform_research(
            task,
            "filtered",
            {
                "connector": "search_auto",
                "action": "search",
                "query": "contador monotributo",
                "filters": {"country": "ar", "language": "es", "recency": "month"},
            },
        )
    (ddg_url, ddg), (bing_url, bing) = recorder.requests
    assert "duckduckgo" in ddg_url and ddg["kl"] == "ar-es" and ddg["df"] == "m"
    assert "bing" in bing_url and bing["cc"] == "AR" and bing["setlang"] == "es"
    assert "df" not in bing
    first, second = result["search_attempts"]
    assert first["filters_applied"] == ["country", "language", "recency"]
    assert first["filters_dropped"] == []
    assert second["filters_applied"] == ["country", "language"]
    assert second["filters_dropped"] == ["recency"]
    assert result["filters_dropped"] == ["recency"]
    assert result["filters_applied"] == ["country", "language"]
    bing_operation = task.operations.get(operation_key="filtered:backend:bing")
    assert bing_operation.request["filters"] == {"country": "ar", "language": "es"}


@pytest.mark.django_db
@override_settings(CACHES=NO_THROTTLE)
def test_eight_fallback_pairs_fit_an_eight_call_budget_and_the_ninth_is_refused():
    task = research_task(max_calls=8)
    recorder = Recorder()
    results = []
    with (
        AgentTools(task, tools_for_role("finder")) as bridge,
        patch("discovery.connectors.web.httpx.get", side_effect=recorder),
    ):
        for number in range(9):
            response = bridge.handle(
                {
                    "id": number,
                    "method": "tools/call",
                    "params": {
                        "name": "research",
                        "arguments": {
                            "connector": "search_auto",
                            "action": "search",
                            "query": f"query {number}",
                        },
                    },
                }
            )
            results.append(json.loads(response["content"][0]["text"]))
    assert [len(result["search_attempts"]) for result in results[:8]] == [2] * 8
    assert [result["research_calls_remaining"] for result in results[:8]] == list(range(7, -1, -1))
    assert results[8]["status"] == "research_limit_reached"
    assert len(recorder.requests) == 16
    assert task.operations.count() == 16 and research_calls_used(task) == 8


def test_filters_are_described_to_the_roles():
    description = ResearchInput.model_json_schema()["properties"]["filters"]["description"]
    for word in ("country", "language", "recency", "day", "week", "month", "year"):
        assert word in description
    research = next(
        tool["function"]
        for tool in tools_for_role("finder")
        if tool["function"]["name"] == "research"
    )
    assert "filters_dropped" in research["description"]
