"""General search and bounded public-web retrieval."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx
from django.conf import settings

from core.html import html_to_text
from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ConnectorError,
    ResearchDocument,
    ResearchPage,
)

_USER_AGENT = "OpportunityFinder/0.2 (read-only opportunity research)"
_MAX_BYTES = 200_000
_TIMEOUT_SECONDS = 20
_MAX_REDIRECTS = 5
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _status_page(status_code: int) -> ResearchPage | None:
    if status_code == 404:
        return ResearchPage(status="not_found", error_reason="Source was not found")
    if status_code in (401, 403):
        return ResearchPage(status="access_denied", error_reason="Source denied access")
    if status_code == 429:
        return ResearchPage(status="rate_limited", error_reason="Source rate limited this request")
    return None


def public_url_address(url: str) -> tuple[str, str, int, str]:
    """Resolve once, reject every non-public answer, then return a pinned address."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorError("Web fetch requires an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConnectorError("Web fetch does not allow URL credentials")
    host = parsed.hostname.encode("idna").decode("ascii")
    if "%" in host or host.lower().rstrip(".") in ("localhost", "metadata.google.internal"):
        raise ConnectorError("Web fetch refuses local or metadata targets")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise ConnectorError("Web fetch permits only public HTTP(S) ports 80 and 443")
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [str(answer[4][0]) for answer in answers]
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ConnectorError("Web fetch refuses non-public IP addresses")
    return parsed.scheme, host, port, addresses[0]


def fetch_public_url(url: str) -> ResearchPage:
    """Pin DNS through connection establishment and revalidate every redirect.

    http.client uses the original hostname for TLS SNI/certificate verification,
    while the overridden connection factory connects only to the checked IP.
    No proxy environment, second DNS lookup, or redirect can bypass that check.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        scheme, host, port, address = public_url_address(current)
        parsed = urlsplit(current)
        connection: http.client.HTTPConnection
        if scheme == "https":
            connection = http.client.HTTPSConnection(
                host, port, timeout=_TIMEOUT_SECONDS, context=ssl.create_default_context()
            )
        else:
            connection = http.client.HTTPConnection(host, port, timeout=_TIMEOUT_SECONDS)

        def connect_pinned(
            address_pair: tuple[str, int],
            timeout: float | None = None,
            source_address: tuple[str, int] | None = None,
            *,
            pinned: str = address,
        ) -> socket.socket:
            return socket.create_connection((pinned, address_pair[1]), timeout, source_address)

        connection._create_connection = connect_pinned  # type: ignore[attr-defined]
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.request(
                "GET", path, headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ConnectorError("Redirect did not supply a Location header")
                current = urljoin(current, location)
                continue
            expected_error = _status_page(response.status)
            if expected_error:
                return expected_error
            if not 200 <= response.status < 300:
                raise ConnectorError(f"Web source returned HTTP {response.status}")
            content_type = (response.getheader("Content-Type") or "").lower()
            if not any(value in content_type for value in ("text/html", "text/plain", "xhtml")):
                raise ConnectorError("Web source is not supported HTML or plain text")
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding != "identity":
                raise ConnectorError("Web source ignored the uncompressed response requirement")
            raw = response.read(_MAX_BYTES + 1)
            truncated = len(raw) > _MAX_BYTES
            charset_match = re.search(r"charset=[\"']?([\w-]+)", content_type)
            charset = charset_match[1] if charset_match else "utf-8"
            text = raw[:_MAX_BYTES].decode(charset, errors="replace")
            title = _TITLE.search(text)
            title_text = html_to_text(title[1]) if title else current
            body = html_to_text(text) if "html" in content_type else text
            return ResearchPage(
                documents=[
                    ResearchDocument(
                        connector="web_fetch",
                        external_id=current,
                        url=current,
                        title=title_text,
                        text=body,
                        kind="fetched_page",
                        metadata={"truncated": truncated, "content_type": content_type},
                    )
                ],
                truncated=truncated,
            )
        finally:
            connection.close()
    raise ConnectorError(f"Web source exceeded {_MAX_REDIRECTS} redirects")


class WebFetchConnector(Connector):
    id = "web_fetch"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Public web pages",
            capabilities=["fetch"],
            access_limitations="HTML/plain text only; 200 KB; public ports 80/443; five redirects",
        )

    def fetch(self, target: str) -> ResearchPage:
        return fetch_public_url(target)


class TavilyConnector(Connector):
    id = "web_search"

    def describe(self) -> ConnectorDescription:
        configured = bool(getattr(settings, "TAVILY_API_KEY", ""))
        return ConnectorDescription(
            id=self.id,
            name="General web search (Tavily)",
            capabilities=["search"],
            supported_filters=["include_domains", "exclude_domains"],
            configuration_requirements=["TAVILY_API_KEY"],
            configured=configured,
            configuration_error=None if configured else "TAVILY_API_KEY is not configured",
            access_limitations="Basic search, at most 20 results; no pagination",
            search_cost_upper_bound_usd=Decimal(
                str(getattr(settings, "DISCOVERY_TAVILY_SEARCH_COST_USD", "0.01"))
            ),
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit)
        for name, value in supplied.items():
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"{name} must be a list of domain strings")
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
                **supplied,
            },
            timeout=_TIMEOUT_SECONDS,
        )
        expected_error = _status_page(response.status_code)
        if expected_error:
            return expected_error
        if not response.is_success:
            raise ConnectorError(f"Tavily search returned HTTP {response.status_code}")
        data = response.json()
        documents = [
            ResearchDocument(
                connector=self.id,
                external_id=item["url"],
                url=item["url"],
                title=item.get("title", ""),
                text=item.get("content", ""),
                kind="search_snippet",
                metadata={"score": item.get("score"), "query": query},
            )
            for item in data.get("results", [])[:limit]
        ]
        return ResearchPage(
            documents=documents,
            truncated=len(documents) == limit,
            cost_usd=self.describe().search_cost_upper_bound_usd,
            cost_is_estimate=True,
        )


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.active: dict[str, str] | None = None
        self.capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            href = attr.get("href") or ""
            target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
            self.active = {"url": target, "title": "", "snippet": ""}
            self.results.append(self.active)
            self.capture = "title"
        elif "result__snippet" in classes and self.active is not None:
            self.capture = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "div"):
            self.capture = None

    def handle_data(self, data: str) -> None:
        if self.active is not None and self.capture:
            self.active[self.capture] += data


class DuckDuckGoConnector(Connector):
    id = "duckduckgo"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="General web search (DuckDuckGo HTML)",
            capabilities=["search"],
            access_limitations=(
                "Public HTML endpoint; best effort availability; bot challenges raise errors; "
                "no pagination; explicitly select instead of Tavily, never a fallback"
            ),
            minimum_interval_seconds=2.0,
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        self.validate_search(query, filters, cursor, limit)
        # The fixed search endpoint is trusted; result URLs remain untrusted.
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        expected_error = _status_page(response.status_code)
        if expected_error:
            return expected_error
        if response.status_code != 200:
            raise ConnectorError(f"DuckDuckGo search returned HTTP {response.status_code}")
        parser = _DuckDuckGoParser()
        parser.feed(response.text[:1_000_000])
        if not parser.results and ("anomaly" in response.text or "challenge" in response.text):
            raise ConnectorError("DuckDuckGo presented a bot challenge; search was not performed")
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=item["url"],
                    url=item["url"],
                    title=item["title"].strip(),
                    text=item["snippet"].strip(),
                    kind="search_snippet",
                    metadata={"query": query},
                )
                for item in parser.results[:limit]
                if urlsplit(item["url"]).scheme in ("http", "https")
            ],
            truncated=len(parser.results) >= limit,
        )
