"""Tool registrations for the investigation agent.

Tools that are still stubs raise ``NotImplementedError``; tools that have
been implemented (currently: ``query_cluster``) run real code. The wrapper
in ``Tool.dispatch`` runs Pydantic validation first, so every tool's schema
already works regardless of impl status.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from django.core.exceptions import ValidationError as DjangoValidationError
from pgvector.django import CosineDistance
from pydantic import BaseModel

from agents.tools import register
from agents.tools.base import Tool, ToolInput, ToolOutput
from clusters.models import Cluster
from core.html import html_to_text
from investigations.schemas import Brief

NOT_YET = "TODO(v1-followup): implement in the tool-impl session"
_HN_TIMEOUT_S = 20.0


# ---------------------------------------------------------------------------
# Internal / cluster-query tools
# ---------------------------------------------------------------------------
class ClusterItemBrief(BaseModel):
    """A compact representative of a cluster member.

    The full ``raw_text`` is intentionally omitted — the agent should call
    ``fetch_hn_item`` or ``fetch_url`` when it needs the full content of a
    specific item, rather than have every cluster query blow up its context.
    """

    id: str
    source: str
    source_item_id: str
    url: str
    title: str | None = None
    author: str | None = None
    posted_at: str
    snippet: str
    classifier_confidence: float


class ClusterSummary(BaseModel):
    id: str
    status: str
    title: str | None = None
    summary: str | None = None
    size: int
    sources: list[str]
    category_tags: list[str]
    classifier_score: float
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    key_items: list[ClusterItemBrief]


class QueryClusterInput(ToolInput):
    cluster_id: str
    # How many representative items to include. The agent can fetch the full
    # content of any specific item via fetch_hn_item / fetch_url.
    max_items: int = 5


class QueryClusterOutput(ToolOutput):
    cluster: ClusterSummary | None = None


def _query_cluster(inp: QueryClusterInput) -> QueryClusterOutput:
    """Look up a cluster by id and return its summary plus key members.

    Returns ``status='not_found'`` when the id does not match a cluster (or
    is not a valid UUID). This is the tool's structured-error contract with
    the agent, not a fallback — the agent uses the signal to self-correct.
    """
    try:
        cluster = Cluster.objects.get(pk=inp.cluster_id)
    except (  # allow: suppress-exception
        Cluster.DoesNotExist,
        ValueError,
        DjangoValidationError,
    ) as exc:
        return QueryClusterOutput(
            status="not_found",
            error_reason=f"No cluster with id {inp.cluster_id!r} ({exc.__class__.__name__})",
        )

    items = list(cluster.items.order_by("-classifier_confidence", "-posted_at")[: inp.max_items])
    summary = ClusterSummary(
        id=str(cluster.id),
        status=cluster.status,
        title=cluster.title,
        summary=cluster.summary,
        size=cluster.size,
        sources=list(cluster.sources),
        category_tags=list(cluster.category_tags),
        classifier_score=cluster.classifier_score,
        first_seen_at=cluster.first_seen_at.isoformat() if cluster.first_seen_at else None,
        last_seen_at=cluster.last_seen_at.isoformat() if cluster.last_seen_at else None,
        key_items=[
            ClusterItemBrief(
                id=str(item.id),
                source=item.source,
                source_item_id=item.source_item_id,
                url=item.url,
                title=item.title,
                author=item.author,
                posted_at=item.posted_at.isoformat(),
                snippet=item.snippet,
                classifier_confidence=item.classifier_confidence,
            )
            for item in items
        ],
    )
    return QueryClusterOutput(status="success", cluster=summary)


register(
    Tool(
        name="query_cluster",
        description=(
            "Return the cluster's summary, title, sources, classifier score, and a "
            "small sample of representative member items (highest classifier "
            "confidence first). The first tool the investigation agent should call."
        ),
        input_type=QueryClusterInput,
        output_type=QueryClusterOutput,
        impl=_query_cluster,
        cost_tier=0,
        cache_ttl_seconds=0,
    )
)


class QueryRelatedClustersInput(ToolInput):
    cluster_id: str
    limit: int = 5


class RelatedCluster(BaseModel):
    id: str
    title: str | None = None
    summary: str | None = None
    size: int
    status: str
    last_seen_at: str | None = None
    similarity: float


class QueryRelatedClustersOutput(ToolOutput):
    related: list[RelatedCluster] = []


def _query_related_clusters(inp: QueryRelatedClustersInput) -> QueryRelatedClustersOutput:
    """Find the nearest active clusters to the given one by centroid similarity.

    Excludes the source cluster and any cluster without a centroid (newly
    created singletons that haven't been refined yet). Uses pgvector's cosine
    distance.
    """
    try:
        source = Cluster.objects.get(pk=inp.cluster_id)
    except (  # allow: suppress-exception
        Cluster.DoesNotExist,
        ValueError,
        DjangoValidationError,
    ) as exc:
        return QueryRelatedClustersOutput(
            status="not_found",
            error_reason=f"No cluster with id {inp.cluster_id!r} ({exc.__class__.__name__})",
        )
    if source.centroid_embedding is None:
        return QueryRelatedClustersOutput(
            status="success",
            related=[],
            error_reason="Source cluster has no centroid yet.",
        )

    qs = (
        Cluster.active.exclude(pk=source.pk)
        .exclude(centroid_embedding__isnull=True)
        .annotate(_dist=CosineDistance("centroid_embedding", source.centroid_embedding))
        .order_by("_dist")[: inp.limit]
    )
    related = [
        RelatedCluster(
            id=str(c.id),
            title=c.title,
            summary=c.summary,
            size=c.size,
            status=c.status,
            last_seen_at=c.last_seen_at.isoformat() if c.last_seen_at else None,
            similarity=round(1.0 - float(c._dist), 4),
        )
        for c in qs
    ]
    return QueryRelatedClustersOutput(status="success", related=related)


register(
    Tool(
        name="query_related_clusters",
        description=(
            "Find other clusters whose centroids are nearest to the given cluster — "
            "useful for spotting prior or adjacent investigations of the same need."
        ),
        input_type=QueryRelatedClustersInput,
        output_type=QueryRelatedClustersOutput,
        impl=_query_related_clusters,
        cost_tier=0,
    )
)


class QueryKnownCompetitorsInput(ToolInput):
    cluster_id: str


class QueryKnownCompetitorsOutput(ToolOutput):
    competitors: list[dict] = []


def _query_known_competitors(_inp: QueryKnownCompetitorsInput) -> QueryKnownCompetitorsOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="query_known_competitors",
        description="Surface competitors previously catalogued for this problem space.",
        input_type=QueryKnownCompetitorsInput,
        output_type=QueryKnownCompetitorsOutput,
        impl=_query_known_competitors,
        cost_tier=0,
    )
)


# ---------------------------------------------------------------------------
# External search/fetch tools
# ---------------------------------------------------------------------------
class SearchHackerNewsInput(ToolInput):
    query: str
    limit: int = 10


class HNSearchHit(BaseModel):
    object_id: str
    type: str
    title: str | None = None
    url: str | None = None
    author: str | None = None
    posted_at: str
    points: int | None = None
    num_comments: int | None = None
    snippet: str = ""


class SearchHackerNewsOutput(ToolOutput):
    results: list[HNSearchHit] = []


def _hn_search_hit_type(hit: dict) -> str:
    tags = hit.get("_tags") or []
    for t in ("story", "comment", "poll", "job", "ask_hn", "show_hn"):
        if t in tags:
            return t
    return "unknown"


def _search_hacker_news(inp: SearchHackerNewsInput) -> SearchHackerNewsOutput:
    """Search Hacker News via Algolia for stories and comments matching the query."""
    response = httpx.get(
        "https://hn.algolia.com/api/v1/search",
        params={"query": inp.query, "hitsPerPage": inp.limit},
        timeout=_HN_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    hits = []
    for hit in data.get("hits", []):
        snippet_raw = hit.get("story_text") or hit.get("comment_text") or ""
        snippet = html_to_text(snippet_raw)[:400]
        object_id = str(hit["objectID"])
        hits.append(
            HNSearchHit(
                object_id=object_id,
                type=_hn_search_hit_type(hit),
                title=hit.get("title"),
                url=hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                author=hit.get("author"),
                posted_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC).isoformat(),
                points=hit.get("points"),
                num_comments=hit.get("num_comments"),
                snippet=snippet,
            )
        )
    return SearchHackerNewsOutput(status="success", results=hits)


register(
    Tool(
        name="search_hacker_news",
        description=(
            "Search Hacker News (stories and comments) via the Algolia search API. "
            "Use this to find external corroboration of the cluster's pain."
        ),
        input_type=SearchHackerNewsInput,
        output_type=SearchHackerNewsOutput,
        impl=_search_hacker_news,
        cost_tier=1,
        cache_ttl_seconds=3600,
    )
)


class FetchHNItemInput(ToolInput):
    item_id: int
    max_children: int = 50


class HNComment(BaseModel):
    id: str
    author: str | None = None
    posted_at: str | None = None
    text: str = ""


class HNItem(BaseModel):
    id: str
    type: str
    title: str | None = None
    url: str | None = None
    author: str | None = None
    posted_at: str | None = None
    points: int | None = None
    text: str = ""
    num_comments: int | None = None
    top_children: list[HNComment] = []


class FetchHNItemOutput(ToolOutput):
    item: HNItem | None = None


def _flatten_children(node: dict | None, out: list[HNComment], limit: int) -> None:
    """Flatten the comment tree breadth-first up to ``limit`` items."""
    if node is None:
        return
    queue: list[dict] = list(node.get("children") or [])
    while queue and len(out) < limit:
        child = queue.pop(0)
        out.append(
            HNComment(
                id=str(child.get("id")),
                author=child.get("author"),
                posted_at=child.get("created_at"),
                text=html_to_text(child.get("text") or ""),
            )
        )
        queue.extend(child.get("children") or [])


def _fetch_hn_item(inp: FetchHNItemInput) -> FetchHNItemOutput:
    """Fetch one HN item (story/comment) plus a bounded slice of its descendants."""
    response = httpx.get(
        f"https://hn.algolia.com/api/v1/items/{inp.item_id}",
        timeout=_HN_TIMEOUT_S,
    )
    if response.status_code == 404:
        return FetchHNItemOutput(
            status="not_found",
            error_reason=f"No HN item with id {inp.item_id}",
        )
    response.raise_for_status()
    data = response.json()
    children: list[HNComment] = []
    _flatten_children(data, children, inp.max_children)
    item = HNItem(
        id=str(data.get("id")),
        type=str(data.get("type") or "unknown"),
        title=data.get("title"),
        url=data.get("url"),
        author=data.get("author"),
        posted_at=data.get("created_at"),
        points=data.get("points"),
        text=html_to_text(data.get("text") or ""),
        num_comments=data.get("num_comments"),
        top_children=children,
    )
    return FetchHNItemOutput(status="success", item=item)


register(
    Tool(
        name="fetch_hn_item",
        description=(
            "Fetch a Hacker News story or comment by id, including a bounded slice "
            "of its descendant comments. Use this to read the full content of an "
            "item that a search result only previewed."
        ),
        input_type=FetchHNItemInput,
        output_type=FetchHNItemOutput,
        impl=_fetch_hn_item,
        cost_tier=1,
        cache_ttl_seconds=86_400,
    )
)


class SearchGitHubIssuesInput(ToolInput):
    query: str
    limit: int = 20


class SearchGitHubIssuesOutput(ToolOutput):
    results: list[dict] = []


def _search_github_issues(_inp: SearchGitHubIssuesInput) -> SearchGitHubIssuesOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="search_github_issues",
        description="Search GitHub issues for evidence of the same pain.",
        input_type=SearchGitHubIssuesInput,
        output_type=SearchGitHubIssuesOutput,
        impl=_search_github_issues,
        cost_tier=1,
        cache_ttl_seconds=3600,
    )
)


class SearchStackOverflowInput(ToolInput):
    query: str
    limit: int = 20


class SearchStackOverflowOutput(ToolOutput):
    results: list[dict] = []


def _search_stack_overflow(_inp: SearchStackOverflowInput) -> SearchStackOverflowOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="search_stack_overflow",
        description="Search Stack Overflow for evidence and workarounds.",
        input_type=SearchStackOverflowInput,
        output_type=SearchStackOverflowOutput,
        impl=_search_stack_overflow,
        cost_tier=1,
        cache_ttl_seconds=3600,
    )
)


class SearchProductHuntInput(ToolInput):
    query: str
    limit: int = 10


class SearchProductHuntOutput(ToolOutput):
    results: list[dict] = []


def _search_product_hunt(_inp: SearchProductHuntInput) -> SearchProductHuntOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="search_product_hunt",
        description="Search Product Hunt for products targeting this pain.",
        input_type=SearchProductHuntInput,
        output_type=SearchProductHuntOutput,
        impl=_search_product_hunt,
        cost_tier=1,
        cache_ttl_seconds=86_400,
    )
)


class FetchProductHuntCommentsInput(ToolInput):
    product_slug: str
    limit: int = 50


class FetchProductHuntCommentsOutput(ToolOutput):
    comments: list[dict] = []


def _fetch_product_hunt_comments(
    _inp: FetchProductHuntCommentsInput,
) -> FetchProductHuntCommentsOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="fetch_product_hunt_comments",
        description="Fetch comments for a Product Hunt launch — useful for gauging reception.",
        input_type=FetchProductHuntCommentsInput,
        output_type=FetchProductHuntCommentsOutput,
        impl=_fetch_product_hunt_comments,
        cost_tier=1,
        cache_ttl_seconds=86_400,
    )
)


class WebSearchInput(ToolInput):
    query: str
    limit: int = 10


class WebSearchOutput(ToolOutput):
    results: list[dict] = []


def _web_search(_inp: WebSearchInput) -> WebSearchOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="web_search",
        description="General-purpose web search — for prior-work and competitor discovery.",
        input_type=WebSearchInput,
        output_type=WebSearchOutput,
        impl=_web_search,
        cost_tier=2,
        cache_ttl_seconds=3600,
    )
)


class FetchUrlInput(ToolInput):
    url: str


class FetchUrlOutput(ToolOutput):
    url: str = ""
    title: str | None = None
    content_text: str = ""
    content_truncated: bool = False


# Cap to keep one fetch from blowing up the agent's context. ~200 KB of HTML
# typically flattens to well under 100 KB of text.
_FETCH_MAX_BYTES = 200_000
_FETCH_TIMEOUT_S = 20.0
_TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.IGNORECASE | re.DOTALL)


def _fetch_url(inp: FetchUrlInput) -> FetchUrlOutput:
    """Fetch a URL and return its main text content.

    Bounded by ``_FETCH_MAX_BYTES`` to keep one over-large page from blowing
    up the agent's context window. ``content_truncated`` signals whether the
    cap was hit. Non-2xx responses raise — the agent should treat that as a
    real failure, not a tool-level fallback.
    """
    response = httpx.get(
        inp.url,
        timeout=_FETCH_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": "opportunity-finder/0.1"},
    )
    response.raise_for_status()
    raw = response.content[:_FETCH_MAX_BYTES]
    truncated = len(response.content) > _FETCH_MAX_BYTES
    try:
        body = raw.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:  # allow: suppress-exception
        # Unknown encoding declared in headers — fall back to utf-8 with
        # replacement. This is recovery from an upstream bug, not an error
        # path: the bytes are still served, the agent just sees mojibake.
        body = raw.decode("utf-8", errors="replace")
    title_match = _TITLE_RE.search(body)
    title = html_to_text(title_match.group(1)) if title_match else None
    return FetchUrlOutput(
        status="success",
        url=str(response.url),
        title=title or None,
        content_text=html_to_text(body),
        content_truncated=truncated,
    )


register(
    Tool(
        name="fetch_url",
        description=(
            "Fetch a URL and return its main text content (HTML stripped). "
            "Bounded to ~200KB of source; use when a search snippet isn't enough."
        ),
        input_type=FetchUrlInput,
        output_type=FetchUrlOutput,
        impl=_fetch_url,
        cost_tier=2,
        cache_ttl_seconds=86_400,
    )
)


class QueryTrustMRRInput(ToolInput):
    company_name: str


class QueryTrustMRROutput(ToolOutput):
    revenue_signal: dict | None = None


def _query_trustmrr(_inp: QueryTrustMRRInput) -> QueryTrustMRROutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="query_trustmrr",
        description="Look up a company's revenue signal (TrustMRR or equivalent).",
        input_type=QueryTrustMRRInput,
        output_type=QueryTrustMRROutput,
        impl=_query_trustmrr,
        cost_tier=3,
        cache_ttl_seconds=86_400,
    )
)


class SummarizeTextInput(ToolInput):
    text: str
    max_tokens: int = 256


class SummarizeTextOutput(ToolOutput):
    summary: str | None = None


def _summarize_text(_inp: SummarizeTextInput) -> SummarizeTextOutput:
    raise NotImplementedError(NOT_YET)


register(
    Tool(
        name="summarize_text",
        description=(
            "Compress a verbose tool result into a short summary suitable for "
            "keeping in the agent's working context."
        ),
        input_type=SummarizeTextInput,
        output_type=SummarizeTextOutput,
        impl=_summarize_text,
        cost_tier=2,
        cache_ttl_seconds=0,
    )
)


# ---------------------------------------------------------------------------
# Termination tool — the agent's "I'm done" signal.
# ---------------------------------------------------------------------------
class RecordBriefOutput(ToolOutput):
    pass  # status field on the base is the whole response


def _record_brief(_brief: Brief) -> RecordBriefOutput:
    """Confirm receipt of the brief.

    The loop intercepts ``record_brief`` calls before this impl is reached —
    it extracts the brief from the tool input, persists it to the AgentRun,
    creates the Investigation row, and terminates the loop. This impl exists
    so that if the loop ever does dispatch it, the agent gets a clean
    success acknowledgement.
    """
    return RecordBriefOutput(status="success")


register(
    Tool(
        name="record_brief",
        description=(
            "Record the final opportunity brief and end the investigation. "
            "Call this exactly once when you have answers to all seven "
            "questions in the procedural prompt and are ready to finalize. "
            "The input fields are the brief — see the procedural prompt for "
            "what each field should contain."
        ),
        input_type=Brief,
        output_type=RecordBriefOutput,
        impl=_record_brief,
        cost_tier=0,
        cache_ttl_seconds=0,
    )
)
