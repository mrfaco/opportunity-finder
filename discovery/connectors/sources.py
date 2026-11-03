"""Optional research over existing public sources and the local corpus."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from django.conf import settings
from django.db.models import Q

from agents.tools.stubs import (
    FetchHNItemInput,
    SearchGitHubIssuesInput,
    SearchHackerNewsInput,
    SearchStackOverflowInput,
    _fetch_hn_item,
    _search_github_issues,
    _search_hacker_news,
    _search_stack_overflow,
)
from clusters.models import ClusterItem
from core.html import html_to_text
from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ConnectorError,
    ResearchDocument,
    ResearchPage,
)
from discovery.connectors.web import _status_page


class HackerNewsConnector(Connector):
    id = "hacker_news"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Hacker News discussions",
            capabilities=["search", "fetch"],
            access_limitations=(
                "Search first 20 matches; fetch item with at most 20 descendant comments"
            ),
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        self.validate_search(query, filters, cursor, limit)
        output = _search_hacker_news(SearchHackerNewsInput(query=query, limit=limit))
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=hit.object_id,
                    url=f"https://news.ycombinator.com/item?id={hit.object_id}",
                    title=hit.title or "HN discussion",
                    text=hit.snippet,
                    kind="search_snippet",
                    author=hit.author,
                    published_at=hit.posted_at,
                    metadata={"original_url": hit.url, "points": hit.points, "query": query},
                )
                for hit in output.results
            ],
            truncated=len(output.results) == limit,
        )

    def fetch(self, target: str) -> ResearchPage:
        if target.isdigit():
            item_id = int(target)
        else:
            parsed = urlsplit(target)
            if parsed.hostname != "news.ycombinator.com":
                raise ValueError("HN fetch requires an item ID or news.ycombinator.com URL")
            value = parse_qs(parsed.query).get("id", [""])[0]
            if not value.isdigit():
                raise ValueError("HN URL does not contain an integer item ID")
            item_id = int(value)
        output = _fetch_hn_item(FetchHNItemInput(item_id=item_id, max_children=20))
        if output.status == "not_found":
            return ResearchPage(status="not_found", error_reason="HN item was not found")
        item = output.item
        if item is None:
            raise ConnectorError("HN returned success without an item")
        body = (
            item.text
            + "\n\n"
            + "\n\n".join(
                f"Comment {child.id} by {child.author or 'unknown'}:\n{child.text}"
                for child in item.top_children
            )
        )
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=item.id,
                    url=f"https://news.ycombinator.com/item?id={item.id}",
                    title=item.title or "HN discussion",
                    text=body.strip(),
                    kind="source_record",
                    author=item.author,
                    published_at=item.posted_at,
                    metadata={"original_url": item.url, "comment_limit": 20},
                )
            ]
        )


class GitHubConnector(Connector):
    id = "github"

    def describe(self) -> ConnectorDescription:
        configured = bool(getattr(settings, "GITHUB_TOKEN", ""))
        return ConnectorDescription(
            id=self.id,
            name="GitHub public issues",
            capabilities=["search", "fetch"],
            configuration_requirements=["GITHUB_TOKEN"],
            configured=configured,
            configuration_error=None if configured else "GITHUB_TOKEN is not configured",
            access_limitations=(
                "Public issues only; first 20 results; subject to GitHub search quotas"
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
        output = _search_github_issues(SearchGitHubIssuesInput(query=query, limit=limit))
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=hit.url,
                    url=hit.url,
                    title=hit.title,
                    text=hit.snippet,
                    kind="search_snippet",
                    published_at=hit.created_at or None,
                    metadata={"repo": hit.repo, "state": hit.state, "query": query},
                )
                for hit in output.results
            ],
            truncated=len(output.results) == limit,
        )

    def fetch(self, target: str) -> ResearchPage:
        self.check_configuration()
        parsed = urlsplit(target)
        if parsed.hostname != "github.com" or parsed.scheme != "https":
            raise ValueError("GitHub fetch requires an https://github.com public issue URL")
        match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/issues/(\d+)/?", parsed.path)
        if not match:
            raise ValueError("GitHub URL must identify one issue")
        owner, repo, number = match.groups()
        response = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
            headers={
                "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "opportunity-finder/0.2",
            },
            timeout=20,
            follow_redirects=False,
        )
        expected = _status_page(response.status_code)
        if expected:
            return expected
        response.raise_for_status()
        item = response.json()
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=f"{owner}/{repo}#{number}",
                    url=item["html_url"],
                    title=item.get("title", ""),
                    text=item.get("body") or "",
                    kind="source_record",
                    author=(item.get("user") or {}).get("login"),
                    published_at=item.get("created_at"),
                    metadata={"repo": f"{owner}/{repo}", "comments": item.get("comments", 0)},
                )
            ]
        )


class StackExchangeConnector(Connector):
    id = "stack_exchange"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Stack Overflow questions",
            capabilities=["search", "fetch"],
            access_limitations="Stack Overflow site; first 20 results; optional STACKEXCHANGE_KEY",
            minimum_interval_seconds=1.0,
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        self.validate_search(query, filters, cursor, limit)
        output = _search_stack_overflow(SearchStackOverflowInput(query=query, limit=limit))
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=hit.url,
                    url=hit.url,
                    title=hit.title,
                    text=hit.title,
                    kind="search_snippet",
                    published_at=hit.creation_date,
                    metadata={"tags": hit.tags, "answer_count": hit.answer_count, "query": query},
                )
                for hit in output.results
            ],
            truncated=len(output.results) == limit,
        )

    def fetch(self, target: str) -> ResearchPage:
        if target.isdigit():
            question_id = target
        else:
            parsed = urlsplit(target)
            if parsed.hostname != "stackoverflow.com":
                raise ValueError("Stack Exchange fetch requires a Stack Overflow question URL/ID")
            match = re.match(r"/questions/(\d+)(?:/|$)", parsed.path)
            if not match:
                raise ValueError("Stack Overflow URL does not identify a question")
            question_id = match[1]
        params = {"site": "stackoverflow", "filter": "withbody"}
        if getattr(settings, "STACKEXCHANGE_KEY", ""):
            params["key"] = settings.STACKEXCHANGE_KEY
        response = httpx.get(
            f"https://api.stackexchange.com/2.3/questions/{question_id}", params=params, timeout=20
        )
        expected = _status_page(response.status_code)
        if expected:
            return expected
        # API-key values occur in request query strings; do not expose raw
        # HTTPStatusError/request URLs to report or operator exception handlers.
        if not response.is_success:
            raise ConnectorError(f"Stack Exchange returned HTTP {response.status_code}")
        data = response.json()
        if data.get("error_id"):
            raise ConnectorError(f"Stack Exchange returned API error {data['error_id']}")
        if data.get("backoff"):
            return ResearchPage(
                status="rate_limited",
                error_reason="Stack Exchange requested backoff",
                retry_after_seconds=int(data["backoff"]),
            )
        items = data.get("items", [])
        if not items:
            return ResearchPage(status="not_found", error_reason="Question was not found")
        item = items[0]
        return ResearchPage(
            documents=[
                ResearchDocument(
                    connector=self.id,
                    external_id=question_id,
                    url=item["link"],
                    title=html_to_text(item.get("title", "")),
                    text=html_to_text(item.get("body", "")),
                    kind="source_record",
                    author=(item.get("owner") or {}).get("display_name"),
                    published_at=datetime.fromtimestamp(item["creation_date"], tz=UTC).isoformat(),
                )
            ]
        )


class LocalCorpusConnector(Connector):
    id = "local_corpus"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Previously ingested local discussions",
            capabilities=["search", "fetch"],
            supported_filters=["source"],
            access_limitations=(
                "Read-only existing ClusterItem corpus; no embedding or ingestion calls"
            ),
            minimum_interval_seconds=0,
        )

    @staticmethod
    def _document(item: ClusterItem, *, snippet: bool) -> ResearchDocument:
        return ResearchDocument(
            connector="local_corpus",
            external_id=str(item.pk),
            url=item.url,
            title=item.title or "Stored discussion",
            text=item.snippet if snippet else item.raw_text,
            kind="search_snippet" if snippet else "internal_record",
            author=item.author,
            published_at=item.posted_at.isoformat(),
            metadata={"source": item.source, "cluster_id": str(item.cluster_id)},
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit, pagination=True)
        offset = int(cursor or "0")
        if offset < 0:
            raise ValueError("Local corpus cursor must be nonnegative")
        items = ClusterItem.objects.filter(Q(title__icontains=query) | Q(raw_text__icontains=query))
        if "source" in supplied:
            if not isinstance(supplied["source"], str):
                raise ValueError("Local corpus source filter must be a string")
            items = items.filter(source=supplied["source"])
        records = list(items.order_by("-posted_at", "pk")[offset : offset + limit + 1])
        has_more = len(records) > limit
        return ResearchPage(
            documents=[self._document(item, snippet=True) for item in records[:limit]],
            next_cursor=str(offset + limit) if has_more else None,
            truncated=has_more,
        )

    def fetch(self, target: str) -> ResearchPage:
        item = ClusterItem.objects.filter(pk=target).first()
        if item is None:
            return ResearchPage(status="not_found", error_reason="Local corpus item was not found")
        return ResearchPage(documents=[self._document(item, snippet=False)])
