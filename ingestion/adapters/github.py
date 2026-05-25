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

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
from django.conf import settings

from ingestion.adapters.base import IngestedItem, SourceAdapter

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.github.com/search/issues"
_HITS_PER_PAGE = 100
# GitHub search caps results at 1000; 10 pages × 100 covers the whole window.
_MAX_PAGES = 10
_REQUEST_TIMEOUT_S = 20.0

# Rate-limit handling. GitHub signals throttling two ways:
#   * Primary: 403 with ``X-RateLimit-Remaining: 0`` and ``X-RateLimit-Reset``
#     (unix timestamp). Resets hourly for the search API.
#   * Secondary / abuse: 403 or 429 with ``Retry-After`` (seconds).
# A bare 403 with neither header is a permission error and must still raise.
_RATE_LIMIT_STATUSES = frozenset({403, 429})
_MAX_RETRIES = 2  # 3 attempts total per page
_MAX_WAIT_S = 60.0  # longer waits → raise so Celery worker isn't pinned


class GitHubRateLimitError(RuntimeError):
    """GitHub asked us to back off for longer than we're willing to wait."""


def _rate_limit_wait(response: httpx.Response) -> float | None:
    """Return seconds to wait if this is a rate-limit response, else None.

    Honors ``Retry-After`` (secondary limit) first, then falls back to
    ``X-RateLimit-Reset`` (primary limit). A status in the rate-limit set
    without either header is a permission/auth error — return None so the
    caller raises.
    """
    if response.status_code not in _RATE_LIMIT_STATUSES:
        return None
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        return max(0.0, float(retry_after))
    if response.headers.get("X-RateLimit-Remaining") == "0":
        reset = response.headers.get("X-RateLimit-Reset")
        if reset is not None:
            return max(0.0, float(reset) - time.time())
    return None


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
        """One page of the ``/search/issues`` endpoint (newest first).

        Retries up to ``_MAX_RETRIES`` times on 403/429 rate-limit responses,
        honoring ``Retry-After`` / ``X-RateLimit-Reset``. Bails loudly if the
        requested wait exceeds ``_MAX_WAIT_S`` — the scheduled run will pick
        up from the checkpoint next tick.
        """
        params: dict[str, str | int] = {
            "q": query,
            "sort": "created",
            "order": "desc",
            "per_page": _HITS_PER_PAGE,
            "page": page,
        }
        for attempt in range(_MAX_RETRIES + 1):
            response = httpx.get(
                _SEARCH_URL,
                params=params,
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT_S,
            )
            wait_s = _rate_limit_wait(response)
            if wait_s is None:
                response.raise_for_status()
                return response.json()
            if wait_s > _MAX_WAIT_S:
                raise GitHubRateLimitError(
                    f"GitHub asked for {wait_s:.0f}s backoff (status "
                    f"{response.status_code}); cap is {_MAX_WAIT_S:.0f}s"
                )
            if attempt == _MAX_RETRIES:
                raise GitHubRateLimitError(
                    f"GitHub still rate-limiting after {_MAX_RETRIES + 1} attempts "
                    f"(status {response.status_code})"
                )
            logger.warning(
                "github rate-limited (status=%s, page=%s); sleeping %.1fs (attempt %s/%s)",
                response.status_code,
                page,
                wait_s,
                attempt + 1,
                _MAX_RETRIES + 1,
            )
            time.sleep(wait_s)
        # Loop always returns or raises; this line satisfies mypy.
        raise AssertionError("unreachable")

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
        # ``is:issue`` is required by the authenticated /search/issues
        # endpoint — GitHub rejects the query with 422 otherwise. Pin it
        # here so the env-tunable INGEST_GITHUB_QUERY can't accidentally
        # remove it.
        query = f"{settings.INGEST_GITHUB_QUERY} is:issue created:>{since_str}"

        for page in range(1, _MAX_PAGES + 1):
            data = self._search(query, page)
            hits = data.get("items", [])
            if not hits:
                return
            for hit in hits:
                yield self._to_item(hit)
            if len(hits) < _HITS_PER_PAGE:
                return
