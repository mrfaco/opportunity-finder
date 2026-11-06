"""reddit: listing parsing, filters, thread fetch, OAuth mode and typed failures, from fixtures."""

import base64

import httpx
import pytest
from django.core.cache import cache
from django.test import override_settings

from discovery.connectors.base import UnsupportedCapabilityError
from discovery.connectors.reddit import RedditConnector
from discovery.research import perform_research
from discovery.source_types import classify
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    provider_fixture,
    research_task,
)

LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "reddit"}
}
OAUTH = {"REDDIT_CLIENT_ID": "app-id", "REDDIT_CLIENT_SECRET": "app-secret", "CACHES": LOCMEM}


def _search(stub, *args, **kwargs):
    with stub.patch():
        return RedditConnector().search(*args, **kwargs)


def test_search_parses_self_posts_as_records_and_link_posts_as_snippets():
    stub = ProviderStub(json_reply(provider_fixture("reddit_search")))
    page = _search(stub, "monotributo contador", limit=2)
    request = stub.requests[0]
    assert request["url"] == "https://www.reddit.com/search.json"
    assert request["params"] == {
        "q": "monotributo contador",
        "limit": 2,
        "sort": "relevance",
        "raw_json": 1,
    }
    assert "Authorization" not in request["headers"]
    record, link = page.documents
    assert record.kind == "source_record"
    assert record.text.startswith("We pay an accountant 45,000 ARS")
    assert record.url == (
        "https://www.reddit.com/r/smallbusiness/comments/1gx1a2b/"
        "paying_too_much_for_monotributo_bookkeeping/"
    )
    assert record.external_id == "t3_1gx1a2b" and record.author == "panaderia_cba"
    assert record.published_at == "2026-01-01T00:00:00+00:00"
    assert record.metadata == {
        "subreddit": "smallbusiness",
        "score": 87,
        "num_comments": 41,
        "permalink": record.url.removeprefix("https://www.reddit.com"),
        "is_self": True,
        "link_url": None,
        "query": "monotributo contador",
    }
    assert classify(record.connector, record.kind, record.url).source_type == "forum"
    assert link.kind == "search_snippet" and link.text == "AFIP raises monotributo categories again"
    assert (
        link.metadata["link_url"]
        == "https://www.lanacion.com.ar/economia/monotributo-categorias-2026/"
    )
    assert page.next_cursor == "t3_1gx2b7k" and page.truncated


def test_filters_and_cursor_map_to_the_subreddit_listing():
    stub = ProviderStub(json_reply(provider_fixture("reddit_search")))
    _search(
        stub,
        "precio contador",
        {"subreddit": "merval", "recency": "month", "sort": "top"},
        cursor="t3_1gx2b7k",
    )
    request = stub.requests[0]
    assert request["url"] == "https://www.reddit.com/r/merval/search.json"
    assert request["params"] == {
        "q": "precio contador",
        "limit": 5,
        "sort": "top",
        "raw_json": 1,
        "t": "month",
        "after": "t3_1gx2b7k",
        "restrict_sr": 1,
    }


@pytest.mark.parametrize(
    ("filters", "error"),
    [
        ({"subreddit": "a"}, "subreddit must be"),
        ({"subreddit": "r/x; drop"}, "subreddit must be"),
        ({"sort": "hot"}, "sort must be one of"),
        ({"recency": "hour"}, "recency must be one of"),
        ({"country": "ar"}, "unsupported filters"),
    ],
)
def test_invalid_filters_are_refused_before_any_request(filters, error):
    stub = ProviderStub()
    with pytest.raises((ValueError, UnsupportedCapabilityError), match=error):
        _search(stub, "q", filters)
    assert stub.requests == []


@pytest.mark.parametrize(
    "target",
    [
        "1gx1a2b",
        "t3_1gx1a2b",
        "https://www.reddit.com/r/smallbusiness/comments/1gx1a2b/paying_too_much/",
        "https://old.reddit.com/r/smallbusiness/comments/1gx1a2b",
    ],
)
def test_fetch_returns_post_and_top_level_comments_by_score(target):
    stub = ProviderStub(json_reply(provider_fixture("reddit_thread")))
    with stub.patch():
        page = RedditConnector().fetch(target)
    request = stub.requests[0]
    assert request["url"] == "https://www.reddit.com/comments/1gx1a2b.json"
    assert request["params"] == {"limit": 40, "depth": 1, "sort": "top", "raw_json": 1}
    (document,) = page.documents
    assert document.kind == "source_record"
    text = document.text
    assert text.startswith("We pay an accountant")
    assert (
        text.index("Comment by ferreteria_mdp (score 54)")
        < text.index("Comment by contadora_ros (score 12)")
        < text.index("Comment by [deleted] (score -3)")
    )
    assert document.metadata["comment_limit"] == 40
    assert document.metadata["comments_included"] == 3
    assert "query" not in document.metadata
    assert len(document.content_hash) == 64 and document.content_hash != ""


@pytest.mark.parametrize(
    "target",
    ["https://evil.example/r/x/comments/abc", "http://www.reddit.com/r/xx/comments/abc", "../etc"],
)
def test_fetch_refuses_other_targets(target):
    with pytest.raises(ValueError, match="post ID or a reddit.com"):
        RedditConnector().fetch(target)


def test_denial_names_oauth_and_rate_limit_honours_retry_after():
    page = _search(ProviderStub(httpx.Response(403)), "q")
    assert page.status == "access_denied"
    assert page.error_reason == "Reddit denied this client; configure OAuth credentials"
    page = _search(ProviderStub(httpx.Response(429, headers={"Retry-After": "30"})), "q")
    assert page.status == "rate_limited" and page.retry_after_seconds == 30


def test_every_provider_failure_is_typed_for_search_and_fetch():
    assert_typed_failures(lambda: RedditConnector().search("q"))
    assert_typed_failures(lambda: RedditConnector().fetch("1gx1a2b"))


def test_unexpected_shapes_are_typed_errors():
    for body in ({"kind": "Listing"}, {"data": {"children": [{"data": {"title": "x"}}]}}):
        assert _search(ProviderStub(json_reply(body)), "q").status == "error"
    with ProviderStub(json_reply({"not": "a thread"})).patch():
        assert RedditConnector().fetch("1gx1a2b").status == "error"


@override_settings(**OAUTH)
def test_oauth_mode_fetches_a_token_once_and_uses_oauth_host():
    cache.clear()
    token = {"access_token": "tok-1", "token_type": "bearer", "expires_in": 86400, "scope": "*"}
    listing = provider_fixture("reddit_search")
    stub = ProviderStub(json_reply(token), json_reply(listing), json_reply(listing))
    with stub.patch():
        RedditConnector().search("q")
        RedditConnector().search("q2")
    token_request, first, second = stub.requests
    assert token_request["method"] == "POST"
    assert token_request["url"] == "https://www.reddit.com/api/v1/access_token"
    assert token_request["data"] == {"grant_type": "client_credentials"}
    expected = base64.b64encode(b"app-id:app-secret").decode()
    assert token_request["headers"]["Authorization"] == f"Basic {expected}"
    for request in (first, second):
        assert request["url"] == "https://oauth.reddit.com/search.json"
        assert request["headers"]["Authorization"] == "bearer tok-1"
    description = RedditConnector().describe()
    assert "OAuth app" in description.name and description.minimum_interval_seconds == 1.0
    assert "app-secret" not in description.model_dump_json()


@override_settings(**OAUTH)
def test_oauth_token_failure_and_denial_are_typed():
    cache.clear()
    page = _search(ProviderStub(httpx.Response(401)), "q")
    assert page.status == "access_denied" and "Reddit OAuth" in page.error_reason
    token = {"access_token": "tok-2", "expires_in": 3600}
    page = _search(ProviderStub(json_reply(token), httpx.Response(403)), "q")
    assert page.status == "access_denied" and "check REDDIT_CLIENT_ID" in page.error_reason


def test_modes_and_configuration_are_described_honestly():
    description = RedditConnector().describe()
    assert description.configured and "unauthenticated" in description.name
    assert description.minimum_interval_seconds == 6.0
    assert set(description.supported_filters) == {"subreddit", "recency", "sort"}
    with override_settings(REDDIT_CLIENT_ID="only-id", REDDIT_CLIENT_SECRET=""):
        half = RedditConnector().describe()
        assert not half.configured and "both be set" in half.configuration_error


@pytest.mark.django_db
@override_settings(CACHES=LOCMEM)
def test_research_service_records_reddit_posts_as_forum_evidence():
    task = research_task(connectors=["search_auto", "web_fetch", "reddit"])
    with ProviderStub(json_reply(provider_fixture("reddit_search"))).patch():
        result = perform_research(
            task, "reddit-1", {"connector": "reddit", "action": "search", "query": "monotributo"}
        )
    assert result["status"] == "success"
    types = dict(task.discovery.evidence.values_list("external_id", "source_type"))
    assert types == {"t3_1gx1a2b": "forum", "t3_1gx2b7k": "search_snippet"}
