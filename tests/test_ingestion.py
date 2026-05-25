"""Tests for the Hacker News adapter and the ingestion pipeline.

The Algolia HTTP call, the classifier, and the embedding provider are all
mocked — these tests never hit the network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from clusters.models import EMBEDDING_DIM, Cluster, ClusterItem
from core.html import html_to_text
from ingestion import pipeline
from ingestion.adapters.base import IngestedItem
from ingestion.adapters.hacker_news import HackerNewsAdapter
from ingestion.filter import FilterVerdict
from ingestion.models import FilterClassification, IngestionCheckpoint, VerdictBand


# ---------------------------------------------------------------------------
# HackerNewsAdapter — mocked Algolia response
# ---------------------------------------------------------------------------
def test_html_to_text_flattens_light_html():
    raw = "<p>I wish there was a tool.<p>The &quot;workaround&quot; is awful &amp; slow."
    text = html_to_text(raw)
    assert "<p>" not in text
    assert '"workaround"' in text
    assert "&amp;" not in text


def _algolia_hit(object_id: str, created_at_i: int, title: str, body: str) -> dict:
    return {
        "objectID": object_id,
        "created_at_i": created_at_i,
        "title": title,
        "story_text": body,
        "author": "someone",
        "points": 42,
        "num_comments": 7,
    }


def test_hn_adapter_parses_hits(monkeypatch):
    payload = {
        "hits": [
            _algolia_hit("100", 1_700_000_000, "Ask HN: a tool?", "<p>I need X."),
        ],
        "nbPages": 1,
        "page": 0,
    }
    monkeypatch.setattr(
        "ingestion.adapters.hacker_news.httpx.get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload),
    )
    items = list(HackerNewsAdapter().fetch_new_items(since=datetime(2023, 1, 1, tzinfo=UTC)))
    assert len(items) == 1
    item = items[0]
    assert item.source == "hacker_news"
    assert item.source_item_id == "100"
    assert item.url == "https://news.ycombinator.com/item?id=100"
    assert "Ask HN: a tool?" in item.raw_text
    assert "I need X." in item.raw_text
    assert item.metadata["points"] == 42


def test_hn_adapter_paginates(monkeypatch):
    pages = {
        0: {"hits": [_algolia_hit("1", 1_700_000_100, "t1", "b1")], "nbPages": 2, "page": 0},
        1: {"hits": [_algolia_hit("2", 1_700_000_050, "t2", "b2")], "nbPages": 2, "page": 1},
    }

    def fake_get(url, params, timeout):  # noqa: ARG001
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: pages[params["page"]])

    monkeypatch.setattr("ingestion.adapters.hacker_news.httpx.get", fake_get)
    items = list(HackerNewsAdapter().fetch_new_items(since=datetime(2023, 1, 1, tzinfo=UTC)))
    assert {i.source_item_id for i in items} == {"1", "2"}


# ---------------------------------------------------------------------------
# GitHubAdapter — mocked Search API response
# ---------------------------------------------------------------------------
from ingestion.adapters.github import GitHubAdapter  # noqa: E402


def _github_hit(
    number: int,
    repo: str,
    title: str,
    body: str,
    *,
    created_at: str = "2026-05-01T12:00:00Z",
    reactions: int = 0,
    labels: tuple[str, ...] = ("enhancement",),
) -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "created_at": created_at,
        "user": {"login": "someone"},
        "comments": 3,
        "state": "open",
        "reactions": {"total_count": reactions},
        "labels": [{"name": lab} for lab in labels],
    }


def test_github_adapter_parses_hits(monkeypatch):
    payload = {
        "items": [
            _github_hit(42, "acme/tool", "Add CSV export", "Users want CSV exports of the report."),
        ],
        "total_count": 1,
    }
    captured = {}

    def fake_get(url, params, headers, timeout):  # noqa: ARG001
        captured["params"] = params
        captured["headers"] = headers
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr("ingestion.adapters.github.httpx.get", fake_get)
    items = list(GitHubAdapter().fetch_new_items(since=datetime(2026, 1, 1, tzinfo=UTC)))

    assert len(items) == 1
    item = items[0]
    assert item.source == "github"
    assert item.source_item_id == "acme/tool#42"
    assert item.url == "https://github.com/acme/tool/issues/42"
    assert item.title == "Add CSV export"
    assert "CSV exports" in item.raw_text
    assert item.author == "someone"
    assert item.metadata["repo"] == "acme/tool"
    assert item.metadata["labels"] == ["enhancement"]
    # Query string was assembled with the configured base, the always-on
    # ``is:issue`` qualifier (required by GitHub for authenticated calls),
    # and the created window.
    assert "is:issue" in captured["params"]["q"]
    assert "created:>2026-01-01" in captured["params"]["q"]
    assert captured["params"]["sort"] == "created"
    assert captured["params"]["order"] == "desc"


def test_github_adapter_paginates(monkeypatch):
    # Two pages of 100 each, then one short page that ends the loop.
    pages = {
        1: {"items": [_github_hit(i, "acme/tool", f"t{i}", "b") for i in range(1, 101)]},
        2: {"items": [_github_hit(i, "acme/tool", f"t{i}", "b") for i in range(101, 201)]},
        3: {"items": [_github_hit(201, "acme/tool", "t201", "b")]},  # short page → stop
    }

    def fake_get(url, params, headers, timeout):  # noqa: ARG001
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: pages[params["page"]])

    monkeypatch.setattr("ingestion.adapters.github.httpx.get", fake_get)
    items = list(GitHubAdapter().fetch_new_items(since=datetime(2026, 1, 1, tzinfo=UTC)))
    assert len(items) == 201


def test_github_adapter_sends_auth_header_when_token_set(monkeypatch, settings):
    settings.GITHUB_TOKEN = "test-pat-xyz"
    captured = {}

    def fake_get(url, params, headers, timeout):  # noqa: ARG001
        captured["headers"] = headers
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": []})

    monkeypatch.setattr("ingestion.adapters.github.httpx.get", fake_get)
    list(GitHubAdapter().fetch_new_items(since=datetime(2026, 1, 1, tzinfo=UTC)))
    assert captured["headers"]["Authorization"] == "Bearer test-pat-xyz"


def test_github_adapter_omits_auth_header_when_token_blank(monkeypatch, settings):
    settings.GITHUB_TOKEN = ""
    captured = {}

    def fake_get(url, params, headers, timeout):  # noqa: ARG001
        captured["headers"] = headers
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": []})

    monkeypatch.setattr("ingestion.adapters.github.httpx.get", fake_get)
    list(GitHubAdapter().fetch_new_items(since=datetime(2026, 1, 1, tzinfo=UTC)))
    assert "Authorization" not in captured["headers"]


# ---------------------------------------------------------------------------
# Pipeline — mocked adapter / classifier / embeddings
# ---------------------------------------------------------------------------
class _FakeAdapter:
    """A SourceAdapter stand-in yielding a fixed list of items."""

    source = "hacker_news"

    def __init__(self, items: list[IngestedItem]) -> None:
        self._items = items

    def fetch_new_items(self, since=None):  # noqa: ARG002
        return iter(self._items)


def _ingested(sid: str, posted_at: datetime, text: str) -> IngestedItem:
    return IngestedItem(
        source="hacker_news",
        source_item_id=sid,
        url=f"https://news.ycombinator.com/item?id={sid}",
        title=f"Ask HN: {sid}",
        author="someone",
        posted_at=posted_at,
        raw_text=text,
        metadata={"points": 10},
    )


def _verdict(is_opp: bool, confidence: float = 0.9) -> FilterVerdict:
    return FilterVerdict(
        is_opportunity=is_opp,
        confidence=confidence,
        reason="mocked",
        prompt_hash="h" * 64,
        model="claude-haiku-4-5",
        input_tokens=120,
        output_tokens=18,
        cached_tokens=0,
        latency_ms=10,
    )


def _patch_classify(monkeypatch, verdict_for):
    monkeypatch.setattr(pipeline, "classify_content", lambda text: verdict_for(text))


def _patch_embed(monkeypatch):
    monkeypatch.setattr(pipeline, "compute_embedding", lambda text: [0.01] * EMBEDDING_DIM)


@pytest.mark.django_db
def test_pipeline_opportunity_creates_cluster_item(monkeypatch):
    base = timezone.now() - timedelta(hours=2)
    adapter = _FakeAdapter([_ingested("1", base, "I wish a tool existed for X")])
    _patch_classify(monkeypatch, lambda text: _verdict(is_opp=True, confidence=0.95))
    _patch_embed(monkeypatch)

    stats = pipeline.ingest_from_adapter(adapter)

    assert stats == {
        "source": "hacker_news",
        "items_processed": 1,
        "opportunities": 1,
        "discarded": 0,
    }
    item = ClusterItem.objects.get(source_item_id="1")
    assert item.cluster is not None
    assert Cluster.objects.count() == 1
    fc = FilterClassification.objects.get(item=item)
    assert fc.is_opportunity is True
    assert fc.discarded is False
    assert fc.verdict_band == VerdictBand.HIGH_YES
    assert fc.cost_usd > 0


@pytest.mark.django_db
def test_pipeline_non_opportunity_records_discarded_classification(monkeypatch):
    base = timezone.now() - timedelta(hours=1)
    adapter = _FakeAdapter([_ingested("2", base, "Just a news announcement")])
    _patch_classify(monkeypatch, lambda text: _verdict(is_opp=False, confidence=0.92))
    _patch_embed(monkeypatch)

    stats = pipeline.ingest_from_adapter(adapter)

    assert stats["opportunities"] == 0
    assert stats["discarded"] == 1
    assert ClusterItem.objects.count() == 0
    fc = FilterClassification.objects.get()
    assert fc.item is None
    assert fc.discarded is True
    assert fc.verdict_band == VerdictBand.HIGH_NO


@pytest.mark.django_db
def test_pipeline_advances_checkpoint(monkeypatch):
    t1 = timezone.now() - timedelta(hours=3)
    t2 = timezone.now() - timedelta(hours=1)
    # Yielded newest-first; the pipeline must process oldest-first.
    adapter = _FakeAdapter([_ingested("b", t2, "newer"), _ingested("a", t1, "older")])
    _patch_classify(monkeypatch, lambda text: _verdict(is_opp=False))
    _patch_embed(monkeypatch)

    pipeline.ingest_from_adapter(adapter)

    checkpoint = IngestionCheckpoint.objects.get(source="hacker_news")
    assert checkpoint.last_item_posted_at == t2  # advanced to the newest item
    assert checkpoint.items_seen == 2
    assert checkpoint.last_run_at is not None


@pytest.mark.django_db
def test_pipeline_uncertain_band_for_low_confidence(monkeypatch):
    base = timezone.now() - timedelta(hours=1)
    adapter = _FakeAdapter([_ingested("3", base, "maybe a need")])
    _patch_classify(monkeypatch, lambda text: _verdict(is_opp=True, confidence=0.6))
    _patch_embed(monkeypatch)

    pipeline.ingest_from_adapter(adapter)

    fc = FilterClassification.objects.get()
    assert fc.verdict_band == VerdictBand.UNCERTAIN


@pytest.mark.django_db
def test_ingest_source_rejects_unknown_source():
    from ingestion.tasks import ingest_source

    with pytest.raises(ValueError, match="No ingestion adapter"):
        ingest_source("myspace")


# ---------------------------------------------------------------------------
# setup_schedules command
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# backfill_source command
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_backfill_skips_existing_and_processes_new(monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    # Pre-seed an item we should skip.
    base = timezone.now() - timedelta(days=10)
    existing_adapter = _FakeAdapter([_ingested("existing", base, "old item already in DB")])
    _patch_classify(monkeypatch, lambda text: _verdict(is_opp=True, confidence=0.95))
    _patch_embed(monkeypatch)
    pipeline.ingest_from_adapter(existing_adapter)
    assert ClusterItem.objects.filter(source_item_id="existing").exists()

    # Now backfill — adapter yields the existing one plus a new one.
    new_item_at = timezone.now() - timedelta(days=20)
    backfill_adapter = _FakeAdapter(
        [
            _ingested("existing", base, "should be skipped"),
            _ingested("new", new_item_at, "brand new opportunity"),
        ]
    )
    monkeypatch.setattr(
        "ingestion.tasks.ADAPTERS",
        {"hacker_news": lambda: backfill_adapter},
    )

    out = StringIO()
    call_command("backfill_source", "hacker_news", "--days", "30", stdout=out)
    output = out.getvalue()

    # 1 processed (the new one), 1 opportunity, out of 2 fetched.
    assert "1 processed" in output
    assert "out of 2 fetched" in output
    assert ClusterItem.objects.filter(source_item_id="new").exists()
    # Existing item was not double-classified.
    assert ClusterItem.objects.filter(source_item_id="existing").count() == 1


def test_backfill_rejects_unknown_source():
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="No ingestion adapter"):
        call_command("backfill_source", "myspace", "--days", "30")


def test_backfill_rejects_nonpositive_days():
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="must be positive"):
        call_command("backfill_source", "hacker_news", "--days", "0")


# ---------------------------------------------------------------------------
# setup_schedules command
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_setup_schedules_creates_periodic_tasks():
    from django.core.management import call_command
    from django_celery_beat.models import PeriodicTask

    call_command("setup_schedules")
    assert PeriodicTask.objects.filter(name="Ingest Hacker News").exists()
    assert PeriodicTask.objects.filter(name="Refine clusters nightly").exists()

    # Idempotent — a second run does not duplicate.
    call_command("setup_schedules")
    assert PeriodicTask.objects.filter(name="Ingest Hacker News").count() == 1
