"""GitHub issues ingestion adapter — feature requests across public repos.

Uses the GitHub Search API ``/search/issues`` filtered to issues matching
``INGEST_GITHUB_QUERY`` (default: ``label:enhancement is:open``). Feature
requests phrased as "I wish this tool did X" match the classifier's notion
of an unmet need better than bug reports do, which tend to be too narrow.

GitHub caps search results at 1000 per query, so for the incremental window
we sort by ``created`` and walk pages newest-first. The pipeline's dedupe
on ``(source, source_item_id)`` keeps reruns safe.

Auth is via ``GITHUB_TOKEN`` (recommended; lifts rate limit from 10 to 30
requests/min). The adapter works unauthenticated but a heavy run can blow
through 10/min in seconds.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
from django.conf import settings

from ingestion.adapters.base import IngestedItem, SourceAdapter

_SEARCH_URL = "https://api.github.com/search/issues"
_HITS_PER_PAGE = 100
# GitHub search caps results at 1000; 10 pages × 100 covers the whole window.
_MAX_PAGES = 10
_REQUEST_TIMEOUT_S = 20.0


class GitHubAdapter(SourceAdapter):
    """Fetch recent GitHub issues as ``IngestedItem`` records."""

    source = "github"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "opportunity-finder/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = settings.GITHUB_TOKEN
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _search(self, query: str, page: int) -> dict:
        """One page of the ``/search/issues`` endpoint (newest first)."""
        response = httpx.get(
            _SEARCH_URL,
            params={
                "q": query,
                "sort": "created",
                "order": "desc",
                "per_page": _HITS_PER_PAGE,
                "page": page,
            },
            headers=self._headers(),
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()

    def _to_item(self, hit: dict) -> IngestedItem:
        # ``owner/repo#number`` is the stable identifier — survives forks/renames
        # in a way the issue's numeric id alone wouldn't, and reads well in logs.
        repo_url = hit.get("repository_url", "")
        repo = "/".join(repo_url.rsplit("/", 2)[-2:]) if repo_url else "unknown/unknown"
        number = hit["number"]
        item_id = f"{repo}#{number}"
        title = hit.get("title") or ""
        body = hit.get("body") or ""
        raw_text = f"{title}\n\n{body}".strip()
        # GitHub returns timestamps like ``2026-05-25T10:30:00Z`` — Python's
        # fromisoformat needs the offset form.
        posted_at = datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00"))
        return IngestedItem(
            source=self.source,
            source_item_id=item_id,
            url=hit["html_url"],
            title=title,
            author=(hit.get("user") or {}).get("login"),
            posted_at=posted_at,
            raw_text=raw_text,
            metadata={
                "repo": repo,
                "comments": hit.get("comments", 0),
                "reactions_total": (hit.get("reactions") or {}).get("total_count", 0),
                "labels": [lab.get("name") for lab in hit.get("labels", []) if lab.get("name")],
                "state": hit.get("state"),
            },
        )

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        """Yield GitHub issues posted after ``since`` (newest first)."""
        if since is None:
            since = datetime.now(tz=UTC) - timedelta(days=settings.INGEST_GITHUB_INITIAL_DAYS)
        since_str = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = f"{settings.INGEST_GITHUB_QUERY} created:>{since_str}"

        for page in range(1, _MAX_PAGES + 1):
            data = self._search(query, page)
            hits = data.get("items", [])
            if not hits:
                return
            for hit in hits:
                yield self._to_item(hit)
            if len(hits) < _HITS_PER_PAGE:
                return
