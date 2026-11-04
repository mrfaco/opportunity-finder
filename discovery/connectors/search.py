"""Independent Bing RSS backend and explicit audited search routing."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from core.html import html_to_text
from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
)
from discovery.connectors.web import _TIMEOUT_SECONDS, _USER_AGENT, _status_page


class BingConnector(Connector):
    id = "bing"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="General web search (Bing RSS)",
            capabilities=["search"],
            minimum_interval_seconds=2,
            access_limitations="Public RSS endpoint; best effort, no pagination; up to 10 results",
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        self.validate_search(query, filters, cursor, limit)
        try:
            response = httpx.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"},
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.RequestError:  # allow: suppress-exception — visible search transport outcome
            return ResearchPage(status="error", error_reason="Bing search transport failed")
        error = _status_page(response.status_code, response.headers.get("Retry-After"))
        if error:
            return error
        if response.status_code != 200:
            return ResearchPage(status="error", error_reason=f"Bing HTTP {response.status_code}")
        raw = response.content
        if len(raw) > 1_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            return ResearchPage(status="error", error_reason="Bing returned unsupported XML")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:  # allow: suppress-exception — malformed provider response
            return ResearchPage(status="error", error_reason="Bing did not return readable RSS")
        if root.tag != "rss":
            return ResearchPage(status="error", error_reason="Bing returned a non-RSS response")
        items = root.findall("./channel/item")
        documents = []
        for item in items[: min(limit, 10)]:
            url = item.findtext("link", "")
            if urlsplit(url).scheme not in ("http", "https"):
                continue
            documents.append(
                ResearchDocument(
                    connector=self.id,
                    external_id=url,
                    url=url,
                    title=item.findtext("title", ""),
                    text=html_to_text(item.findtext("description", "")),
                    kind="search_snippet",
                    metadata={"query": query, "backend": self.id},
                )
            )
        return ResearchPage(documents=documents, truncated=len(items) >= min(limit, 10))


class AutoSearchConnector(Connector):
    id = "search_auto"
    version = "1.1"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            version=self.version,
            name="General web search (DuckDuckGo → Bing on failure)",
            capabilities=["search"],
            minimum_interval_seconds=0,
            access_limitations=(
                "Explicit fallback: DuckDuckGo then Bing on rate limit, timeout, access denial "
                "or source error. Each backend attempt is audited and counts against quota; "
                "local cooldown denials do not. No retry loop or challenge bypass."
            ),
        )
