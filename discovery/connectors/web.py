"""General search and bounded public-web retrieval."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
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
    parse_stated_date,
)
from discovery.connectors.pdf import PDF_MAX_BYTES, extract_pdf

_USER_AGENT = "OpportunityFinder/0.2 (read-only opportunity research)"
_MAX_BYTES = 200_000
_TIMEOUT_SECONDS = 20
_MAX_REDIRECTS = 5
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class _PdfLinks(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__()
        self.base = base
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or len(self.links) >= 30:
            return
        target = urljoin(self.base, dict(attrs).get("href") or "")
        parsed = urlsplit(target)
        if (
            parsed.scheme in ("https", "http")
            and parsed.path.lower().endswith(".pdf")
            and target not in self.links
        ):
            self.links.append(target)


_META_DATE_NAMES = ("date", "dc.date", "pubdate")
_JSON_LD_TYPES = {"NewsArticle", "Article", "WebPage"}


class _DateMarkup(HTMLParser):
    """Collect the page's own publication-date statements, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.article_published: list[str] = []
        self.meta_date: list[str] = []
        self.json_ld: list[str] = []
        self.time_published: list[str] = []
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            content = attr.get("content", "")
            if attr.get("property", "").lower() == "article:published_time":
                self.article_published.append(content)
            elif attr.get("name", "").lower() in _META_DATE_NAMES:
                self.meta_date.append(content)
        elif tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self.json_ld.append("")
        elif (
            tag == "time"
            and attr.get("datetime")
            and ("pubdate" in attr or attr.get("itemprop", "").lower() == "datepublished")
        ):
            self.time_published.append(attr["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self.json_ld[-1] += data


def _json_ld_dates(blocks: list[str]) -> list[str]:
    """``datePublished`` of the first NewsArticle/Article/WebPage object."""
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:  # allow: suppress-exception — malformed page JSON-LD
            # Untrusted page markup; other date sources are still tried.
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack = graph + stack
            kinds = item.get("@type")
            names = set(kinds) if isinstance(kinds, list) else {kinds}
            if names & _JSON_LD_TYPES:
                value = item.get("datePublished")
                return [value] if isinstance(value, str) else []
    return []


def _published_at(html: str, headers: dict[str, str]) -> tuple[str, str] | None:
    """The page's stated publication date and where it came from, strongest first.

    ``http_last_modified`` is the weakest signal: a server modification time.
    """
    markup = _DateMarkup()
    if html:
        markup.feed(html)
    candidates = [
        ("meta_article_published", markup.article_published),
        ("meta_date", markup.meta_date),
        ("json_ld_date_published", _json_ld_dates(markup.json_ld)),
        ("time_date_published", markup.time_published),
        ("http_last_modified", [headers.get("Last-Modified", "")]),
    ]
    for source, values in candidates:
        for value in values:
            stated = parse_stated_date(value)
            if stated:
                return stated, source
    return None


def _status_page(status_code: int, retry_after: str | None = None) -> ResearchPage | None:
    if status_code == 404:
        return ResearchPage(status="not_found", error_reason="Source was not found")
    if status_code in (401, 403):
        return ResearchPage(status="access_denied", error_reason="Source denied access")
    if status_code == 429:
        cooldown = (
            min(max(int(retry_after), 1), 3600) if retry_after and retry_after.isdigit() else 60
        )
        return ResearchPage(
            status="rate_limited",
            retry_after_seconds=cooldown,
            error_reason="Source rate limited this request",
        )
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
                if parsed.fragment and not urlsplit(current).fragment:
                    current += "#" + parsed.fragment
                continue
            expected_error = _status_page(response.status, response.getheader("Retry-After"))
            if expected_error:
                return expected_error
            if not 200 <= response.status < 300:
                raise ConnectorError(f"Web source returned HTTP {response.status}")
            content_type = (response.getheader("Content-Type") or "").lower()
            is_pdf = "application/pdf" in content_type or parsed.path.lower().endswith(".pdf")
            if not is_pdf and not any(
                value in content_type
                for value in ("text/html", "text/plain", "xhtml", "application/octet-stream")
            ):
                raise ConnectorError("Web source is not supported HTML, plain text or PDF")
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding != "identity":
                raise ConnectorError("Web source ignored the uncompressed response requirement")
            prefix = response.read(1024)
            is_pdf = is_pdf or prefix.startswith(b"%PDF-")
            byte_limit = PDF_MAX_BYTES if is_pdf else _MAX_BYTES
            raw = prefix + response.read(max(0, byte_limit + 1 - len(prefix)))
            if is_pdf:
                if len(raw) > PDF_MAX_BYTES:
                    raise ConnectorError("PDF exceeds the 20 MB download limit")
                return extract_pdf(raw, current, content_type)
            if "octet-stream" in content_type:
                raise ConnectorError("Binary source is not a PDF")
            truncated = len(raw) > _MAX_BYTES
            charset_match = re.search(r"charset=[\"']?([\w-]+)", content_type)
            charset = charset_match[1] if charset_match else "utf-8"
            text = raw[:_MAX_BYTES].decode(charset, errors="replace")
            title = _TITLE.search(text)
            title_text = html_to_text(title[1]) if title else current
            body = html_to_text(text) if "html" in content_type else text
            links = _PdfLinks(current)
            if "html" in content_type:
                links.feed(text)
            stated = _published_at(
                text if "html" in content_type else "",
                {"Last-Modified": response.getheader("Last-Modified") or ""},
            )
            return ResearchPage(
                documents=[
                    ResearchDocument(
                        connector="web_fetch",
                        connector_version="1.1",
                        external_id=current,
                        url=current,
                        title=title_text,
                        text=body,
                        kind="fetched_page",
                        published_at=stated[0] if stated else None,
                        metadata={
                            "truncated": truncated,
                            "content_type": content_type,
                            "pdf_links": links.links,
                            **({"published_at_source": stated[1]} if stated else {}),
                        },
                    )
                ],
                truncated=truncated,
            )
        finally:
            connection.close()
    raise ConnectorError(f"Web source exceeded {_MAX_REDIRECTS} redirects")


class WebFetchConnector(Connector):
    id = "web_fetch"
    version = "1.1"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            version=self.version,
            name="Public web pages and PDFs",
            capabilities=["fetch"],
            access_limitations=(
                "HTML/text 200 KB; PDF 20 MB, 10 pages per fetch, 120k text; "
                "use #page=N or #page=N-M for later pages; scanned PDFs require OCR; "
                "public ports 80/443; five redirects"
            ),
        )

    def fetch(self, target: str) -> ResearchPage:
        try:
            return fetch_public_url(target)
        except (TimeoutError, subprocess.TimeoutExpired):  # allow: suppress-exception
            # One slow public page is a visible tool outcome. The agent can
            # choose another source rather than losing the whole dialogue.
            return ResearchPage(
                status="timeout", error_reason="Source page timed out; try another source"
            )
        except ConnectorError as exc:  # allow: suppress-exception — visible extraction failure
            return ResearchPage(status="error", error_reason=str(exc))
        except (  # allow: suppress-exception — explicit source-error tool protocol
            OSError,
            http.client.HTTPException,
        ) as exc:  # allow: suppress-exception
            return ResearchPage(
                status="error",
                error_reason=f"Source fetch failed ({type(exc).__name__}); try another source",
            )


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
        expected_error = _status_page(response.status_code, response.headers.get("Retry-After"))
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


# DuckDuckGo's region parameter kl is <country>-<language>; a country alone
# takes its main language from this table. Sorted by country code.
_DDG_DEFAULT_LANGUAGE = {
    "ar": "es",
    "br": "pt",
    "ca": "en",
    "cl": "es",
    "co": "es",
    "de": "de",
    "es": "es",
    "fr": "fr",
    "in": "en",
    "it": "it",
    "mx": "es",
    "pe": "es",
    "pt": "pt",
    "uk": "en",
    "us": "en",
    "uy": "es",
}
_DDG_RECENCY = {"day": "d", "week": "w", "month": "m", "year": "y"}


def duckduckgo_params(query: str, filters: dict[str, Any]) -> dict[str, str]:
    """Validated DuckDuckGo query parameters for the market and period filters."""
    params = {"q": query}
    country = filters.get("country")
    language = filters.get("language")
    if language and not country:
        raise ValueError("duckduckgo: the language filter needs a country (kl=<country>-<lang>)")
    if country:
        language = language or _DDG_DEFAULT_LANGUAGE.get(country)
        if not language:
            raise ValueError(f"duckduckgo: give a language filter for country {country}")
        params["kl"] = f"{country}-{language}"
    if filters.get("recency"):
        params["df"] = _DDG_RECENCY[filters["recency"]]
    return params


class DuckDuckGoConnector(Connector):
    id = "duckduckgo"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="General web search (DuckDuckGo HTML)",
            capabilities=["search"],
            supported_filters=["country", "language", "recency"],
            access_limitations=(
                "Public HTML endpoint; best effort availability; challenges return rate limits; "
                "no pagination; search_auto can try Bing on failure"
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
        params = duckduckgo_params(query, self.validate_search(query, filters, cursor, limit))
        # The fixed search endpoint is trusted; result URLs remain untrusted.
        try:
            response = httpx.get(
                "https://html.duckduckgo.com/html/",
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:  # allow: suppress-exception — visible source outcome
            return ResearchPage(
                status="timeout" if isinstance(exc, httpx.TimeoutException) else "error",
                error_reason="DuckDuckGo search transport failed",
            )
        if response.status_code == 202:
            return self._challenge()
        expected_error = _status_page(response.status_code, response.headers.get("Retry-After"))
        if expected_error:
            return expected_error
        if response.status_code != 200:
            return ResearchPage(
                status="error", error_reason=f"DuckDuckGo HTTP {response.status_code}"
            )
        parser = _DuckDuckGoParser()
        parser.feed(response.text[:1_000_000])
        if not parser.results and ("anomaly" in response.text or "challenge" in response.text):
            return self._challenge()
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

    @staticmethod
    def _challenge() -> ResearchPage:
        return ResearchPage(
            status="rate_limited",
            retry_after_seconds=60,
            error_reason="DuckDuckGo presented a bot challenge; no search was performed. "
            "Respect the cooldown. Use captured evidence or fetch known public sources; "
            "report missing evidence honestly. Do not bypass the challenge.",
        )
