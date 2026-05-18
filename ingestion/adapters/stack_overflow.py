from __future__ import annotations

from datetime import datetime
from typing import Iterator

from .base import IngestedItem, SourceAdapter


class StackOverflowAdapter(SourceAdapter):
    source = "stack_overflow"

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        raise NotImplementedError("TODO(v1-followup): implement Stack Overflow adapter")
