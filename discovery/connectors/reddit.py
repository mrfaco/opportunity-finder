"""Reddit posts and comment threads through Reddit's public JSON listing API."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache

from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
)
from discovery.connectors.http import get_json, post_json

_PUBLIC_HOST = "https://www.reddit.com"
_OAUTH_HOST = "https://oauth.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_POST_ID = re.compile(r"^(?:t3_)?([a-z0-9]{1,12})$")
_PERMALINK = re.compile(r"^/r/[A-Za-z0-9_]{2,21}/comments/([a-z0-9]{1,12})(?:/|$)")
_SORTS = ("relevance", "top", "new", "comments")
_COMMENT_LIMIT = 40
_SHAPE_ERROR = "Reddit returned an unexpected response shape"


def _credentials() -> tuple[str, str]:
    return settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET


def _oauth() -> bool:
    return all(_credentials())


def _created(post: dict[str, Any]) -> str | None:
    value = post.get("created_utc")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    return None


def _listing_children(listing: Any) -> list[dict[str, Any]] | None:
    """The ``data`` of each child of a Reddit Listing, or None when it is not one."""
    if not isinstance(listing, dict) or not isinstance(listing.get("data"), dict):
        return None
    children = listing["data"].get("children")
    if not isinstance(children, list):
        return None
    return [
        child["data"]
        for child in children
        if isinstance(child, dict) and isinstance(child.get("data"), dict)
    ]


class RedditConnector(Connector):
    id = "reddit"

    def describe(self) -> ConnectorDescription:
        client_id, secret = _credentials()
        half_configured = bool(client_id) != bool(secret)
        oauth = _oauth()
        mode = "OAuth app" if oauth else "unauthenticated public JSON"
        error = "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must both be set or both be empty"
        return ConnectorDescription(
            id=self.id,
            name=f"Reddit posts and comments ({mode}; filters subreddit, recency, sort)",
            capabilities=["search", "fetch"],
            supported_filters=["subreddit", "recency", "sort"],
            configuration_requirements=["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
            configured=not half_configured,
            configuration_error=error if half_configured else None,
            access_limitations=(
                "Shows what users say about a problem, their workarounds and recurring pain, "
                "not that anyone paid; search returns at most 20 posts per page (cursor), "
                "fetch returns a post with its top 40 top-level comments by score; "
                "unauthenticated requests from cloud IPs may be denied"
            ),
            cache_ttl_seconds=1800,
            minimum_interval_seconds=1.0 if oauth else 6.0,
        )

    def _headers(self) -> dict[str, str] | ResearchPage:
        if not _oauth():
            return {}
        client_id, secret = _credentials()
        key = "discovery:reddit:token:" + hashlib.sha256(client_id.encode()).hexdigest()[:20]
        token = cache.get(key)
        if token is None:
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            outcome = post_json(
                _TOKEN_URL,
                source="Reddit OAuth",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}"},
            )
            if outcome.error is not None:
                return outcome.error
            data = outcome.data
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                return ResearchPage(status="error", error_reason="Reddit OAuth returned no token")
            token = data["access_token"]
            lifetime = data.get("expires_in")
            ttl = int(lifetime) - 60 if isinstance(lifetime, int) else 0
            if ttl > 0:
                cache.set(key, token, timeout=ttl)
        return {"Authorization": f"bearer {token}"}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        headers = self._headers()
        if isinstance(headers, ResearchPage):
            return headers
        host = _OAUTH_HOST if headers else _PUBLIC_HOST
        outcome = get_json(host + path, source="Reddit", params=params, headers=headers)
        if outcome.error is None:
            return outcome.data
        if outcome.error.status == "access_denied":
            reason = (
                "Reddit denied the configured OAuth client; check REDDIT_CLIENT_ID/SECRET"
                if headers
                else "Reddit denied this client; configure OAuth credentials"
            )
            return outcome.error.model_copy(update={"error_reason": reason})
        return outcome.error

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit, pagination=True)
        subreddit = supplied.get("subreddit")
        if subreddit is not None and not (
            isinstance(subreddit, str) and _SUBREDDIT.match(subreddit)
        ):
            raise ValueError("reddit: subreddit must be 2-21 letters, digits or underscores")
        sort = supplied.get("sort", "relevance")
        if sort not in _SORTS:
            raise ValueError(f"reddit: sort must be one of {', '.join(_SORTS)}")
        params: dict[str, Any] = {"q": query, "limit": limit, "sort": sort, "raw_json": 1}
        if supplied.get("recency"):
            params["t"] = supplied["recency"]
        if cursor:
            params["after"] = cursor
        path = "/search.json"
        if subreddit:
            path = f"/r/{subreddit}/search.json"
            params["restrict_sr"] = 1
        data = self._get(path, params)
        if isinstance(data, ResearchPage):
            return data
        posts = _listing_children(data)
        if posts is None:
            return ResearchPage(status="error", error_reason=_SHAPE_ERROR)
        documents = [self._post_document(post, {"query": query}) for post in posts[:limit]]
        if any(document is None for document in documents):
            return ResearchPage(status="error", error_reason=_SHAPE_ERROR)
        after = data["data"].get("after")
        return ResearchPage(
            documents=[document for document in documents if document is not None],
            next_cursor=after if isinstance(after, str) else None,
            truncated=isinstance(after, str),
        )

    def _post_document(
        self, post: dict[str, Any], extra: dict[str, Any], thread: str | None = None
    ) -> ResearchDocument | None:
        """A post; with ``thread`` (a fetch) its comments follow and it is a full record."""
        permalink = post.get("permalink")
        name = post.get("name")
        if not isinstance(permalink, str) or not isinstance(name, str):
            return None
        body = post.get("selftext") if isinstance(post.get("selftext"), str) else ""
        is_self = bool(post.get("is_self"))
        complete = is_self and bool(body)
        text = body if complete else str(post.get("title") or "")
        return ResearchDocument(
            connector=self.id,
            external_id=name,
            url=_PUBLIC_HOST + permalink,
            title=str(post.get("title") or "Reddit post"),
            text="\n\n".join(part for part in (text, thread) if part),
            kind="source_record" if complete or thread is not None else "search_snippet",
            author=post.get("author") if isinstance(post.get("author"), str) else None,
            published_at=_created(post),
            metadata={
                "subreddit": post.get("subreddit"),
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "permalink": permalink,
                "is_self": is_self,
                "link_url": None if is_self else post.get("url"),
                **extra,
            },
        )

    def fetch(self, target: str) -> ResearchPage:
        self.check_configuration()
        post_id = _POST_ID.match(target)
        if post_id is None:
            parsed = urlsplit(target)
            host = (parsed.hostname or "").lower()
            permalink = _PERMALINK.match(parsed.path)
            if (
                parsed.scheme != "https"
                or host not in ("reddit.com", "www.reddit.com", "old.reddit.com")
                or permalink is None
            ):
                raise ValueError("Reddit fetch requires a post ID or a reddit.com post permalink")
            post_id = permalink
        data = self._get(
            f"/comments/{post_id[1]}.json",
            {"limit": _COMMENT_LIMIT, "depth": 1, "sort": "top", "raw_json": 1},
        )
        if isinstance(data, ResearchPage):
            return data
        if not isinstance(data, list) or len(data) != 2:
            return ResearchPage(status="error", error_reason=_SHAPE_ERROR)
        posts, comments = _listing_children(data[0]), _listing_children(data[1])
        if not posts or comments is None:
            return ResearchPage(status="error", error_reason=_SHAPE_ERROR)
        # Top-level comments only ("more" stubs carry no body), strongest first.
        bodies = sorted(
            (c for c in comments if isinstance(c.get("body"), str)),
            key=lambda c: c["score"] if isinstance(c.get("score"), int) else 0,
            reverse=True,
        )
        thread = "\n\n".join(
            f"Comment by {c.get('author') or 'unknown'} (score {c.get('score', 0)}):\n{c['body']}"
            for c in bodies[:_COMMENT_LIMIT]
        )
        extra = {
            "comment_limit": _COMMENT_LIMIT,
            "comments_included": min(len(bodies), _COMMENT_LIMIT),
        }
        document = self._post_document(posts[0], extra, thread)
        if document is None:
            return ResearchPage(status="error", error_reason=_SHAPE_ERROR)
        return ResearchPage(documents=[document])
