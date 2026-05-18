from __future__ import annotations

from datetime import datetime
from typing import Iterator

from .base import IngestedItem, SourceAdapter


class GitHubAdapter(SourceAdapter):
    source = "github"

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        raise NotImplementedError("TODO(v1-followup): implement GitHub issues adapter")
