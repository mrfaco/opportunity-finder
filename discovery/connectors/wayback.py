"""Internet Archive Wayback Machine captures: whether a competitor existed, changed or died."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
    parse_stated_date,
)
from discovery.connectors.http import get_json
from discovery.connectors.web import WebFetchConnector

_CDX = "https://web.archive.org/cdx/search/cdx"
_SNAPSHOT = re.compile(r"^https://web\.archive\.org/web/(\d{14})(?:id_)?/(https?://\S+)$")
_MATCH_TYPES = ("exact", "prefix")
_FIRST_YEAR = 1996
_ABSENT = (
    "No captures matched; absence from the archive is not absence from the web. "
    "Try match=prefix, the bare host, or other years."
)


def _capture_time(timestamp: str) -> str | None:
    try:
        parsed = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    except ValueError:  # allow: suppress-exception — impossible date in a capture timestamp
        # Fourteen digits that are not a calendar time; the capture stays undated.
        return None
    return parse_stated_date(parsed.replace(tzinfo=UTC).isoformat())


def _original_labels(original: str) -> dict[str, str]:
    """Metadata that makes the research service type and count a capture as its original."""
    return {
        "original_url": original,
        "canonical_url": original,
        "original_source_origin": (urlsplit(original).hostname or "").lower(),
    }


def _year(name: str, value: Any) -> int:
    year = int(value) if isinstance(value, str) and value.isdigit() else value
    if not isinstance(year, int) or not _FIRST_YEAR <= year <= datetime.now(UTC).year:
        raise ValueError(f"wayback: {name} must be a year from {_FIRST_YEAR} to this year")
    return year


class WaybackConnector(Connector):
    id = "wayback"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Web archive captures (Wayback Machine; filters from_year, to_year, match)",
            capabilities=["search", "fetch"],
            supported_filters=["from_year", "to_year", "match"],
            access_limitations=(
                "Shows that a page or site existed and what it said on a date, not that it "
                "sold anything; the query is a URL or host, not keywords; at most one capture "
                "per month, 20 per search, HTTP 200 only; fetch a returned snapshot URL to read "
                "it as archived (typed and counted as the original site); absence from the "
                "archive is not absence from the web"
            ),
            cache_ttl_seconds=86400,
            minimum_interval_seconds=2.0,
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit)
        target = query.strip()
        parsed = urlsplit(target if "://" in target else f"http://{target}")
        if parsed.scheme not in ("http", "https") or not parsed.hostname or " " in target:
            raise ValueError("wayback: the query must be a URL or host name to look up")
        params: dict[str, Any] = {
            "url": target,
            "output": "json",
            "filter": "statuscode:200",
            "collapse": "timestamp:6",
            "limit": limit,
        }
        if "match" in supplied:
            if supplied["match"] not in _MATCH_TYPES:
                raise ValueError("wayback: match must be exact or prefix")
            params["matchType"] = supplied["match"]
        if "from_year" in supplied:
            params["from"] = _year("from_year", supplied["from_year"])
        if "to_year" in supplied:
            params["to"] = _year("to_year", supplied["to_year"])
        if params.get("from", 0) > params.get("to", 9999):
            raise ValueError("wayback: from_year must not be after to_year")
        outcome = get_json(_CDX, source="Wayback CDX", params=params)
        if outcome.error is not None:
            return outcome.error
        rows = outcome.data
        if rows == []:
            return ResearchPage(error_reason=_ABSENT)
        if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
            return ResearchPage(
                status="error", error_reason="Wayback CDX returned an unexpected response shape"
            )
        header = rows[0]
        if not {"timestamp", "original"} <= set(header):
            return ResearchPage(status="error", error_reason="Wayback CDX returned no header row")
        documents = []
        for row in rows[1 : limit + 1]:
            if len(row) != len(header):
                continue
            capture = dict(zip(header, row, strict=True))
            timestamp, original = str(capture["timestamp"]), str(capture["original"])
            if not re.fullmatch(r"\d{14}", timestamp) or not original.startswith("http"):
                continue
            snapshot = f"https://web.archive.org/web/{timestamp}id_/{original}"
            published = _capture_time(timestamp)
            documents.append(
                ResearchDocument(
                    connector=self.id,
                    external_id=f"{timestamp}/{original}",
                    url=snapshot,
                    title=f"Archived {original}",
                    text=(
                        f"Wayback Machine capture of {original} at {published or timestamp} "
                        f"({capture.get('mimetype', 'unknown type')}, HTTP "
                        f"{capture.get('statuscode', '?')}). Fetch {snapshot} to read it."
                    ),
                    kind="source_record",
                    published_at=published,
                    metadata={
                        **_original_labels(original),
                        "mimetype": capture.get("mimetype"),
                        "statuscode": capture.get("statuscode"),
                        "query": query,
                    },
                )
            )
        if not documents:
            return ResearchPage(error_reason=_ABSENT)
        return ResearchPage(documents=documents, truncated=len(rows) - 1 >= limit)

    def fetch(self, target: str) -> ResearchPage:
        match = _SNAPSHOT.match(target)
        if match is None:
            raise ValueError(
                "wayback fetch requires a snapshot URL "
                "https://web.archive.org/web/<14-digit timestamp>[id_]/<original URL>"
            )
        # id_ asks for the archived bytes without the archive's toolbar or link rewriting.
        page = WebFetchConnector().fetch(f"https://web.archive.org/web/{match[1]}id_/{match[2]}")
        if page.status != "success":
            return page
        documents = []
        for document in page.documents:
            # The archive may redirect to the nearest capture; label by the one served.
            served = _SNAPSHOT.match(document.url.split("#", 1)[0])
            timestamp, original = (served[1], served[2]) if served else (match[1], match[2])
            captured = _capture_time(timestamp)
            documents.append(
                ResearchDocument.model_validate(
                    {
                        **document.model_dump(exclude={"content_hash"}),
                        "connector": self.id,
                        "connector_version": self.version,
                        "published_at": document.published_at or captured,
                        "metadata": {
                            **document.metadata,
                            **_original_labels(original),
                            "captured_at": captured,
                            **(
                                {}
                                if document.published_at
                                else {"published_at_source": "wayback_capture"}
                            ),
                        },
                    }
                )
            )
        return page.model_copy(update={"documents": documents})
