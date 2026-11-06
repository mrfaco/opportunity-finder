"""Region-aware general web search through the Brave Search API (key required, opt-in)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from core.html import html_to_text
from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
    parse_stated_date,
)
from discovery.connectors.http import get_json

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
# Brave pages by whole result pages (offset counts pages of `count`), up to page 9.
_MAX_OFFSET = 9


def _search_lang(language: str, country: str | None) -> str:
    # Brave has no bare "pt"; it distinguishes Brazilian and European Portuguese.
    if language == "pt":
        return "pt-br" if country in (None, "br") else "pt-pt"
    return language


class BraveSearchConnector(Connector):
    id = "brave"

    def describe(self) -> ConnectorDescription:
        configured = bool(getattr(settings, "BRAVE_SEARCH_API_KEY", ""))
        return ConnectorDescription(
            id=self.id,
            name="General web search (Brave Search API; filters country, language, recency)",
            capabilities=["search"],
            supported_filters=["country", "language", "recency"],
            configuration_requirements=["BRAVE_SEARCH_API_KEY"],
            configured=configured,
            configuration_error=None if configured else "BRAVE_SEARCH_API_KEY is not configured",
            access_limitations=(
                "Search results are leads, not evidence: fetch a page before relying on it; "
                "at most 20 results per page, ten pages (cursor); free-tier quota applies; "
                "cost is the configured estimate"
            ),
            search_cost_upper_bound_usd=Decimal(
                str(getattr(settings, "DISCOVERY_BRAVE_SEARCH_COST_USD", "0"))
            ),
            minimum_interval_seconds=1.0,
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit, pagination=True)
        if cursor is not None and not (cursor.isdigit() and int(cursor) <= _MAX_OFFSET):
            raise ValueError(f"brave: cursor must be a page number from 0 to {_MAX_OFFSET}")
        offset = int(cursor or "0")
        params: dict[str, Any] = {"q": query, "count": limit, "offset": offset}
        country = supplied.get("country")
        if country:
            params["country"] = country.upper()
        if supplied.get("language"):
            params["search_lang"] = _search_lang(supplied["language"], country)
        if supplied.get("recency"):
            params["freshness"] = _FRESHNESS[supplied["recency"]]
        outcome = get_json(
            _ENDPOINT,
            source="Brave Search",
            params=params,
            headers={"X-Subscription-Token": settings.BRAVE_SEARCH_API_KEY},
        )
        if outcome.error is not None:
            return outcome.error
        data = outcome.data
        if not isinstance(data, dict):
            return ResearchPage(
                status="error", error_reason="Brave Search returned an unexpected response shape"
            )
        web = data.get("web") if isinstance(data.get("web"), dict) else {}
        results = web.get("results", []) if isinstance(web, dict) else []
        if not isinstance(results, list):
            return ResearchPage(
                status="error", error_reason="Brave Search returned an unexpected response shape"
            )
        documents = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
                continue
            stated = next(
                (
                    parse_stated_date(value)
                    for value in (item.get("page_age"), item.get("age"))
                    if isinstance(value, str) and parse_stated_date(value)
                ),
                None,
            )
            documents.append(
                ResearchDocument(
                    connector=self.id,
                    external_id=url,
                    url=url,
                    title=html_to_text(str(item.get("title") or "")),
                    text=html_to_text(str(item.get("description") or "")),
                    kind="search_snippet",
                    published_at=stated,
                    metadata={"query": query, "language": item.get("language")},
                )
            )
        query_info = data.get("query") if isinstance(data.get("query"), dict) else {}
        more = bool(query_info.get("more_results_available")) if query_info else False
        return ResearchPage(
            documents=documents,
            next_cursor=str(offset + 1) if more and offset < _MAX_OFFSET else None,
            truncated=more,
            cost_usd=self.describe().search_cost_upper_bound_usd,
            cost_is_estimate=True,
        )
