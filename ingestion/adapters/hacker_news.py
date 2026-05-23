"""Hacker News ingestion adapter — Ask HN stories.

Uses the Algolia HN Search API (``hn.algolia.com``) rather than the Firebase
API: one paginated query returns Ask HN stories with their text inline and
supports a ``created_at_i`` date filter, so incremental ingestion is a single
query per page instead of walking item trees id-by-id.

Scope is Ask HN stories only — each post's text is usually a need statement
in itself, so the classifier sees every one without a keyword pre-filter.
Comment ingestion is a separate, higher-volume increment (see NEXT_STEPS).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
from django.conf import settings

from core.html import html_to_text
from ingestion.adapters.base import IngestedItem, SourceAdapter

_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
_HITS_PER_PAGE = 100
# Safety cap — one run should never need this many pages of new Ask HN posts.
_MAX_PAGES = 20
_REQUEST_TIMEOUT_S = 20.0


class HackerNewsAdapter(SourceAdapter):
    """Fetch recent Ask HN stories as ``IngestedItem`` records."""

    source = "hacker_news"

    def _search(self, after_epoch: int, page: int) -> dict:
        """One page of the Algolia ``search_by_date`` endpoint (newest first)."""
        response = httpx.get(
            _ALGOLIA_URL,
            params={
                "tags": "ask_hn",
                "numericFilters": f"created_at_i>{after_epoch}",
                "hitsPerPage": _HITS_PER_PAGE,
                "page": page,
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()

    def _to_item(self, hit: dict) -> IngestedItem:
        item_id = str(hit["objectID"])
        title = hit.get("title") or ""
        body = html_to_text(hit.get("story_text") or "")
        raw_text = f"{title}\n\n{body}".strip()
        return IngestedItem(
            source=self.source,
            source_item_id=item_id,
            url=f"https://news.ycombinator.com/item?id={item_id}",
            title=title,
            author=hit.get("author"),
            posted_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
            raw_text=raw_text,
            metadata={
                "points": hit.get("points"),
                "num_comments": hit.get("num_comments"),
            },
        )

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        """Yield Ask HN stories posted after ``since`` (newest first).

        ``since`` defaults to the configured initial window so the adapter is
        safe to call standalone; the ingestion pipeline always passes the
        stored checkpoint.
        """
        if since is None:
            since = datetime.now(tz=UTC) - timedelta(days=settings.INGEST_HN_INITIAL_DAYS)
        after_epoch = int(since.timestamp())

        for page in range(_MAX_PAGES):
            data = self._search(after_epoch, page)
            hits = data.get("hits", [])
            if not hits:
                return
            for hit in hits:
                yield self._to_item(hit)
            if page + 1 >= data.get("nbPages", 1):
                return
