"""Common interface for source adapters.

An adapter pulls items from one external source and yields them as
``IngestedItem`` records. The pipeline downstream applies the classifier and
the clustering step; adapters know nothing about either.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


@dataclass
class IngestedItem:
    source: str
    source_item_id: str
    url: str
    title: str | None
    author: str | None
    posted_at: datetime
    raw_text: str
    metadata: dict


class SourceAdapter(ABC):
    """Base class — one concrete subclass per source."""

    source: str = ""

    @abstractmethod
    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        """Yield items posted after ``since`` (or all if None).

        Implementations should be idempotent — the pipeline dedupes via
        ``(source, source_item_id)`` uniqueness so re-running an adapter never
        produces duplicate rows.
        """
        raise NotImplementedError
