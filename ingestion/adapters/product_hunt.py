from __future__ import annotations

from datetime import datetime
from typing import Iterator

from ingestion.adapters.base import IngestedItem, SourceAdapter


class ProductHuntAdapter(SourceAdapter):
    source = "product_hunt"

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        raise NotImplementedError("TODO(v1-followup): implement Product Hunt adapter")
