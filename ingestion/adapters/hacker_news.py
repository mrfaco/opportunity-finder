from __future__ import annotations

from datetime import datetime
from typing import Iterator

from .base import IngestedItem, SourceAdapter


class HackerNewsAdapter(SourceAdapter):
    """Fetch Ask HN / Show HN / story comments from the Firebase HN API.

    TODO(v1-followup): implement against https://github.com/HackerNews/API.
    Priority items: Ask HN threads, comments containing complaint signals
    (heuristics: "I wish", "annoying", "doesn't work", "is there a tool that").
    """

    source = "hacker_news"

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        raise NotImplementedError(
            "TODO(v1-followup): implement Hacker News adapter — first ingestion adapter target"
        )
