"""Stack Exchange ingestion adapter — questions across SE network sites.

Uses the Stack Exchange API v2.3 ``/questions`` endpoint. The default site
is ``stackoverflow`` but can be pointed at any SE site
(``softwareengineering``, ``serverfault``, ``superuser``, ``askubuntu``…)
via the ``INGEST_STACKEXCHANGE_SITE`` setting — the SE network is fairly
homogeneous in API shape, so one adapter covers all of them.

Stack Exchange's authoritative quota live in two response fields:

* ``backoff`` — when set (seconds), the next request to the same endpoint
  must wait at least that long. This is the "soft" signal — slow down.
* ``quota_remaining`` — the hard daily allowance, 300/day without a key
  or 10000/day with one. We bail loudly when the response carries a 429
  or a 503; daily quota exhaustion shows up that way.

The source name stays ``stack_overflow`` even when the site is
configured to something else — keeping the existing ``Source`` enum
stable until a real per-site split is needed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
from django.conf import settings

from core.html import html_to_text
from ingestion.adapters.base import IngestedItem, SourceAdapter

logger = logging.getLogger(__name__)

_API_BASE = "https://api.stackexchange.com/2.3"
_QUESTIONS_PATH = "/questions"
# Stack Exchange caps page sizes at 100. The withbody built-in filter
# includes the question body which the classifier needs to read.
_HITS_PER_PAGE = 100
# SE returns at most 25 pages × pagesize via the API; for a 30-day
# backfill on a quiet site that's plenty, and a busy site (stackoverflow)
# will return 25 pages of the most recent questions which is what we want.
_MAX_PAGES = 25
_REQUEST_TIMEOUT_S = 20.0
_WITHBODY_FILTER = "withbody"

# Rate-limit handling parallels the GitHub adapter:
# * 429/503 = hard throttle. Read Retry-After if present, else raise.
# * SE's response-level ``backoff`` is a soft signal we honor between
#   pages — we sleep for that many seconds before the next call.
_RATE_LIMIT_STATUSES = frozenset({429, 503})
_MAX_RETRIES = 2  # 3 attempts per page on hard throttling
_MAX_WAIT_S = 60.0  # longer waits → raise so the Celery worker isn't pinned


class StackExchangeRateLimitError(RuntimeError):
    """Stack Exchange asked us to back off for longer than we're willing to wait."""


def _hard_rate_limit_wait(response: httpx.Response) -> float | None:
    """Return seconds to wait if this is a 429/503, else None.

    Honors ``Retry-After`` (seconds). A bare 429/503 without that header
    is treated as an opaque hard limit (returns 0 → next retry attempt
    will likely hit the same wall and we bail at the retry cap).
    """
    if response.status_code not in _RATE_LIMIT_STATUSES:
        return None
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        return max(0.0, float(retry_after))
    return 0.0


class StackOverflowAdapter(SourceAdapter):
    """Fetch recent Stack Exchange questions as ``IngestedItem`` records.

    Kept named ``StackOverflowAdapter`` for backwards compatibility with
    the previous stub; the underlying source label is also unchanged.
    The configured *site* (default stackoverflow) determines which SE
    network site is queried.
    """

    source = "stack_overflow"

    def _params(self, fromdate: int, page: int) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "site": settings.INGEST_STACKEXCHANGE_SITE,
            "fromdate": fromdate,
            "order": "desc",
            "sort": "creation",
            "pagesize": _HITS_PER_PAGE,
            "page": page,
            "filter": _WITHBODY_FILTER,
        }
        tags = settings.INGEST_STACKEXCHANGE_TAGS
        if tags:
            # SE wants semicolon-separated tags. Pass through verbatim;
            # users supply "django;python" etc.
            params["tagged"] = tags
        key = settings.STACKEXCHANGE_KEY
        if key:
            params["key"] = key
        return params

    def _search(self, fromdate: int, page: int) -> dict:
        """One page of ``/questions``.

        Retries up to ``_MAX_RETRIES`` times on 429/503, honoring
        ``Retry-After``. Bails loudly past ``_MAX_WAIT_S`` — the scheduled
        run will pick up from the checkpoint next tick. Permission errors
        with no rate-limit headers still propagate as ``httpx.HTTPStatusError``.
        """
        url = f"{_API_BASE}{_QUESTIONS_PATH}"
        for attempt in range(_MAX_RETRIES + 1):
            response = httpx.get(
                url,
                params=self._params(fromdate, page),
                timeout=_REQUEST_TIMEOUT_S,
                headers={"User-Agent": "opportunity-finder/0.1"},
            )
            wait_s = _hard_rate_limit_wait(response)
            if wait_s is None:
                response.raise_for_status()
                return response.json()
            if wait_s > _MAX_WAIT_S:
                raise StackExchangeRateLimitError(
                    f"Stack Exchange asked for {wait_s:.0f}s backoff "
                    f"(status {response.status_code}); cap is {_MAX_WAIT_S:.0f}s"
                )
            if attempt == _MAX_RETRIES:
                raise StackExchangeRateLimitError(
                    f"Stack Exchange still rate-limiting after {_MAX_RETRIES + 1} "
                    f"attempts (status {response.status_code})"
                )
            logger.warning(
                "stack_exchange rate-limited (status=%s, page=%s); sleeping %.1fs (attempt %s/%s)",
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
        question_id = str(hit["question_id"])
        title = hit.get("title") or ""
        body = html_to_text(hit.get("body") or "")
        raw_text = f"{title}\n\n{body}".strip()
        owner = hit.get("owner") or {}
        return IngestedItem(
            source=self.source,
            source_item_id=question_id,
            url=hit.get("link") or f"https://stackoverflow.com/q/{question_id}",
            title=title,
            author=owner.get("display_name"),
            posted_at=datetime.fromtimestamp(hit["creation_date"], tz=UTC),
            raw_text=raw_text,
            metadata={
                "site": settings.INGEST_STACKEXCHANGE_SITE,
                "tags": hit.get("tags") or [],
                "score": hit.get("score"),
                "view_count": hit.get("view_count"),
                "answer_count": hit.get("answer_count"),
                "is_answered": hit.get("is_answered"),
            },
        )

    def fetch_new_items(self, since: datetime | None = None) -> Iterator[IngestedItem]:
        """Yield Stack Exchange questions posted after ``since`` (newest first).

        Walks pages until ``has_more`` is false or the page cap is reached.
        Between pages we honor the response-level ``backoff`` signal: SE
        wants us to slow down without us hitting a 429 first.
        """
        if since is None:
            since = datetime.now(tz=UTC) - timedelta(
                days=settings.INGEST_STACKEXCHANGE_INITIAL_DAYS
            )
        fromdate = int(since.timestamp())

        # SE pagination is 1-indexed.
        for page in range(1, _MAX_PAGES + 1):
            data = self._search(fromdate, page)
            items = data.get("items", [])
            if not items:
                return
            for hit in items:
                yield self._to_item(hit)
            backoff = data.get("backoff")
            if backoff:
                logger.info(
                    "stack_exchange soft backoff: sleeping %ss before next page",
                    backoff,
                )
                time.sleep(float(backoff))
            if not data.get("has_more"):
                return
