"""Tests for the agent tool implementations.

Tools are dispatched through ``Tool.dispatch`` which runs Pydantic
validation on the input before calling the impl. These tests go through
that public path so the input/output schemas are exercised too.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from agents.tools import get_tool
from clusters.models import (
    EMBEDDING_DIM,
    ClassifierVerdict,
    Cluster,
    ClusterItem,
    ClusterStatus,
    Source,
)


def _vec() -> list[float]:
    return [0.01] * EMBEDDING_DIM


def _make_cluster(**overrides) -> Cluster:
    now = timezone.now()
    defaults = {
        "status": ClusterStatus.PENDING,
        "size": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "sources": [Source.HACKER_NEWS],
        "centroid_embedding": _vec(),
        "title": "Painful PDF tooling",
        "summary": "Multiple users want a better PDF merge experience",
        "category_tags": ["dev-tools"],
        "classifier_score": 0.9,
    }
    defaults.update(overrides)
    return Cluster.objects.create(**defaults)


def _make_item(cluster: Cluster, sid: str, confidence: float, posted_at=None) -> ClusterItem:
    now = posted_at or timezone.now()
    return ClusterItem.objects.create(
        cluster=cluster,
        source=Source.HACKER_NEWS,
        source_item_id=sid,
        url=f"https://news.ycombinator.com/item?id={sid}",
        title=f"Ask HN: item {sid}",
        author="someone",
        posted_at=now,
        raw_text=f"Full body for item {sid} — describes a real unmet need.",
        snippet=f"Snippet for item {sid}",
        classifier_verdict=ClassifierVerdict.OPPORTUNITY,
        classifier_confidence=confidence,
        embedding=_vec(),
        added_to_cluster_at=now,
        assigned_at=now,
    )


# ---------------------------------------------------------------------------
# query_cluster
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_query_cluster_returns_summary_and_key_items():
    cluster = _make_cluster()
    _make_item(cluster, "a", confidence=0.7)
    _make_item(cluster, "b", confidence=0.95)
    _make_item(cluster, "c", confidence=0.8)

    out = get_tool("query_cluster").dispatch({"cluster_id": str(cluster.id)})

    assert out.status == "success"
    assert out.cluster is not None
    assert out.cluster.id == str(cluster.id)
    assert out.cluster.title == "Painful PDF tooling"
    assert out.cluster.summary.startswith("Multiple users")
    assert out.cluster.sources == [Source.HACKER_NEWS]
    assert len(out.cluster.key_items) == 3
    # Ordered by classifier_confidence DESC.
    assert [i.source_item_id for i in out.cluster.key_items] == ["b", "c", "a"]
    # Items carry the public fields the agent needs.
    item = out.cluster.key_items[0]
    assert item.snippet.startswith("Snippet")
    assert item.url.endswith("?id=b")


@pytest.mark.django_db
def test_query_cluster_respects_max_items():
    cluster = _make_cluster()
    for i in range(8):
        _make_item(cluster, str(i), confidence=0.5 + i / 100)

    out = get_tool("query_cluster").dispatch({"cluster_id": str(cluster.id), "max_items": 3})

    assert out.status == "success"
    assert len(out.cluster.key_items) == 3


@pytest.mark.django_db
def test_query_cluster_not_found_for_unknown_id():
    out = get_tool("query_cluster").dispatch({"cluster_id": "00000000-0000-0000-0000-000000000000"})
    assert out.status == "not_found"
    assert out.cluster is None
    assert "No cluster" in out.error_reason


@pytest.mark.django_db
def test_query_cluster_not_found_for_invalid_uuid():
    out = get_tool("query_cluster").dispatch({"cluster_id": "not-a-uuid"})
    assert out.status == "not_found"
    assert out.cluster is None


def test_query_cluster_validation_fails_on_bad_input():
    # Missing required field -> dispatch returns validation_failed.
    out = get_tool("query_cluster").dispatch({})
    assert out.status == "validation_failed"


def test_query_cluster_definition_round_trips_to_json_schema():
    tool = get_tool("query_cluster")
    definition = tool.definition()
    assert definition.name == "query_cluster"
    assert definition.input_schema["type"] == "object"
    # The nested ClusterSummary should show up in the output schema's $defs.
    assert "ClusterSummary" in str(definition.output_schema)


# ---------------------------------------------------------------------------
# query_related_clusters
# ---------------------------------------------------------------------------
def _vec_with(*nonzero: tuple[int, float]) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    for i, x in nonzero:
        v[i] = x
    return v


@pytest.mark.django_db
def test_query_related_clusters_returns_nearest_by_centroid():
    now = timezone.now()
    source = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec_with((0, 1.0)),
        title="src",
    )
    near = Cluster.objects.create(
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec_with((0, 0.99), (1, 0.01)),
        title="near",
    )
    Cluster.objects.create(  # far cluster
        status=ClusterStatus.PENDING,
        size=1,
        first_seen_at=now,
        last_seen_at=now,
        sources=[Source.HACKER_NEWS],
        centroid_embedding=_vec_with((10, 1.0)),
        title="far",
    )

    out = get_tool("query_related_clusters").dispatch({"cluster_id": str(source.id), "limit": 5})

    assert out.status == "success"
    ids = [r.id for r in out.related]
    assert str(source.id) not in ids  # excludes self
    assert ids[0] == str(near.id)  # nearest first
    assert out.related[0].similarity > out.related[-1].similarity


@pytest.mark.django_db
def test_query_related_clusters_not_found_for_unknown():
    out = get_tool("query_related_clusters").dispatch(
        {"cluster_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert out.status == "not_found"


# ---------------------------------------------------------------------------
# search_hacker_news (mocked Algolia)
# ---------------------------------------------------------------------------
def _algolia_search_hit(
    object_id: str,
    title: str | None = None,
    story_text: str | None = None,
    comment_text: str | None = None,
    tags=("story",),
) -> dict:
    return {
        "objectID": object_id,
        "title": title,
        "story_text": story_text,
        "comment_text": comment_text,
        "url": None,
        "author": "alice",
        "created_at_i": 1_700_000_000,
        "points": 50,
        "num_comments": 3,
        "_tags": list(tags),
    }


def test_search_hacker_news_parses_hits(monkeypatch):
    payload = {
        "hits": [
            _algolia_search_hit("1", title="Story", story_text="<p>body</p>", tags=("story",)),
            _algolia_search_hit("2", comment_text="<p>comment body</p>", tags=("comment",)),
        ]
    }
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload),
    )
    out = get_tool("search_hacker_news").dispatch({"query": "X", "limit": 5})
    assert out.status == "success"
    assert [h.object_id for h in out.results] == ["1", "2"]
    assert out.results[0].type == "story"
    assert out.results[1].type == "comment"
    assert "comment body" in out.results[1].snippet
    # Story-without-url should fall back to the HN permalink.
    assert out.results[0].url.endswith("?id=1")


# ---------------------------------------------------------------------------
# fetch_hn_item (mocked Algolia items endpoint)
# ---------------------------------------------------------------------------
def test_fetch_hn_item_flattens_tree_with_bounded_children(monkeypatch):
    payload = {
        "id": 42,
        "type": "story",
        "title": "Ask HN: a tool?",
        "url": None,
        "author": "alice",
        "created_at": "2024-01-01T00:00:00Z",
        "points": 10,
        "text": "<p>need X</p>",
        "num_comments": 3,
        "children": [
            {
                "id": 100,
                "author": "bob",
                "created_at": "2024-01-01T00:01:00Z",
                "text": "<p>same here</p>",
                "children": [
                    {
                        "id": 101,
                        "author": "carol",
                        "created_at": "2024-01-01T00:02:00Z",
                        "text": "<p>+1</p>",
                        "children": [],
                    },
                ],
            },
            {
                "id": 200,
                "author": "dan",
                "created_at": "2024-01-01T00:03:00Z",
                "text": "<p>workaround</p>",
                "children": [],
            },
        ],
    }
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: payload
        ),
    )
    out = get_tool("fetch_hn_item").dispatch({"item_id": 42, "max_children": 10})
    assert out.status == "success"
    assert out.item.id == "42"
    assert out.item.title == "Ask HN: a tool?"
    assert "need X" in out.item.text
    child_ids = [c.id for c in out.item.top_children]
    assert {"100", "101", "200"} <= set(child_ids)


def test_fetch_hn_item_max_children_caps_output(monkeypatch):
    children = [
        {"id": i, "author": "x", "created_at": "t", "text": f"<p>c{i}</p>", "children": []}
        for i in range(20)
    ]
    payload = {"id": 1, "type": "story", "children": children}
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: payload
        ),
    )
    out = get_tool("fetch_hn_item").dispatch({"item_id": 1, "max_children": 5})
    assert out.status == "success"
    assert len(out.item.top_children) == 5


def test_fetch_hn_item_returns_not_found_on_404(monkeypatch):
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(
            status_code=404, raise_for_status=lambda: None, json=lambda: {}
        ),
    )
    out = get_tool("fetch_hn_item").dispatch({"item_id": 999})
    assert out.status == "not_found"
    assert out.item is None


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------
def _fake_url_response(
    html_body: bytes, *, encoding="utf-8", final_url="https://example.com/p"
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        content=html_body,
        encoding=encoding,
        url=final_url,
    )


def test_fetch_url_strips_html_and_extracts_title(monkeypatch):
    body = (
        b"<html><head><title>The Page</title></head>"
        b"<body><script>alert(1)</script><p>hello</p><p>world</p></body></html>"
    )
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: _fake_url_response(body),
    )
    out = get_tool("fetch_url").dispatch({"url": "https://example.com/p"})
    assert out.status == "success"
    assert out.title == "The Page"
    assert "hello" in out.content_text
    assert "world" in out.content_text
    assert "alert" not in out.content_text  # <script> stripped
    assert out.content_truncated is False


def test_fetch_url_marks_truncated_when_oversized(monkeypatch):
    body = b"<p>" + (b"x" * 300_000) + b"</p>"  # over the 200KB cap
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: _fake_url_response(body),
    )
    out = get_tool("fetch_url").dispatch({"url": "https://example.com/p"})
    assert out.status == "success"
    assert out.content_truncated is True


# ---------------------------------------------------------------------------
# web_search (Tavily)
# ---------------------------------------------------------------------------
def _fake_post_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: payload)


def test_web_search_parses_hits(monkeypatch, settings):
    settings.TAVILY_API_KEY = "test-key"
    payload = {
        "results": [
            {
                "url": "https://example.com/a",
                "title": "A page",
                "content": "Snippet about A",
                "score": 0.95,
            },
            {
                "url": "https://example.com/b",
                "title": "B page",
                "content": "Snippet about B",
                "score": 0.82,
            },
        ],
        "answer": "irrelevant — we don't use it",
    }
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.post",
        lambda *a, **k: _fake_post_response(payload),
    )
    out = get_tool("web_search").dispatch({"query": "django migrations", "limit": 5})
    assert out.status == "success"
    assert [h.url for h in out.results] == ["https://example.com/a", "https://example.com/b"]
    assert out.results[0].snippet == "Snippet about A"
    assert out.results[0].score == 0.95


def test_web_search_missing_key_raises(monkeypatch, settings):
    settings.TAVILY_API_KEY = ""
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        get_tool("web_search").dispatch({"query": "anything"})


# ---------------------------------------------------------------------------
# search_github_issues
# ---------------------------------------------------------------------------
def test_search_github_issues_parses_hits(monkeypatch, settings):
    settings.GITHUB_TOKEN = "test-pat"
    payload = {
        "items": [
            {
                "html_url": "https://github.com/django/django/issues/1234",
                "title": "Squashed migrations break on rollback",
                "state": "open",
                "comments": 12,
                "repository_url": "https://api.github.com/repos/django/django",
                "created_at": "2024-01-01T00:00:00Z",
                "body": "When we run rollback after squashing the migrations fail because…",
            },
        ]
    }
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload),
    )
    out = get_tool("search_github_issues").dispatch(
        {"query": "migration squash rollback", "limit": 10}
    )
    assert out.status == "success"
    assert len(out.results) == 1
    hit = out.results[0]
    assert hit.url.endswith("/issues/1234")
    assert hit.repo == "django/django"
    assert hit.state == "open"
    assert hit.comments == 12
    assert hit.snippet.startswith("When we run rollback")


def test_search_github_issues_missing_token_raises(monkeypatch, settings):
    settings.GITHUB_TOKEN = ""
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        get_tool("search_github_issues").dispatch({"query": "anything"})


# ---------------------------------------------------------------------------
# search_stack_overflow
# ---------------------------------------------------------------------------
def test_search_stack_overflow_parses_hits(monkeypatch, settings):
    settings.STACKEXCHANGE_KEY = "test-key"
    payload = {
        "items": [
            {
                "link": "https://stackoverflow.com/q/42",
                "title": "How do I squash migrations?",
                "score": 17,
                "answer_count": 3,
                "is_answered": True,
                "tags": ["django", "migrations"],
                "creation_date": 1_700_000_000,
            },
        ]
    }
    monkeypatch.setattr(
        "agents.tools.stubs.httpx.get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload),
    )
    out = get_tool("search_stack_overflow").dispatch({"query": "squash migrations"})
    assert out.status == "success"
    assert len(out.results) == 1
    hit = out.results[0]
    assert hit.url == "https://stackoverflow.com/q/42"
    assert hit.is_answered is True
    assert "django" in hit.tags
    assert hit.creation_date.startswith("2023-")  # 1_700_000_000 → late 2023


def test_search_stack_overflow_works_without_key(monkeypatch, settings):
    settings.STACKEXCHANGE_KEY = ""
    captured: dict = {}

    def fake_get(url, params, headers, timeout):  # noqa: ARG001
        captured["params"] = params
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": []})

    monkeypatch.setattr("agents.tools.stubs.httpx.get", fake_get)
    out = get_tool("search_stack_overflow").dispatch({"query": "anything"})
    assert out.status == "success"
    assert out.results == []
    # Anonymous request — no `key` query param attached.
    assert "key" not in captured["params"]
