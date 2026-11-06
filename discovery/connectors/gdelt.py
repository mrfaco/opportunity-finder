"""Dated news articles with publisher URLs from the GDELT DOC 2.0 API."""

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

_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT's sourcecountry operator takes FIPS 10-4 codes, which differ from ISO 3166-1
# for some markets (Germany GM, Spain SP). Only mapped countries are accepted so a
# wrong code never silently returns another country's press. Sorted by ISO code.
_FIPS_COUNTRY = {
    "ar": "AR",
    "br": "BR",
    "ca": "CA",
    "cl": "CI",
    "co": "CO",
    "de": "GM",
    "es": "SP",
    "fr": "FR",
    "gb": "UK",
    "in": "IN",
    "it": "IT",
    "mx": "MX",
    "nl": "NL",
    "pe": "PE",
    "pl": "PL",
    "pt": "PO",
    "us": "US",
    "uy": "UY",
}
# sourcelang takes ISO 639-3; extend this table to admit another language.
_ISO639_3 = {
    "de": "deu",
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "hi": "hin",
    "it": "ita",
    "nl": "nld",
    "pl": "pol",
    "pt": "por",
}
_TIMESPAN = {"day": "1d", "week": "1w", "month": "1m", "year": "1y"}
_DEFAULT_TIMESPAN = "3m"
_SEENDATE = re.compile(r"^\d{8}T\d{6}Z$")


def _seen_at(value: Any) -> str | None:
    if not isinstance(value, str) or not _SEENDATE.match(value):
        return None
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return parse_stated_date(parsed.isoformat())


def gdelt_params(query: str, filters: dict[str, Any], limit: int) -> dict[str, Any]:
    """The DOC API request for a query and validated market/period filters."""
    terms = [query.strip()]
    country = filters.get("country")
    if country:
        if country not in _FIPS_COUNTRY:
            raise ValueError(
                f"news_gdelt: country {country} is not mapped; use one of "
                + ", ".join(sorted(_FIPS_COUNTRY))
            )
        terms.append(f"sourcecountry:{_FIPS_COUNTRY[country]}")
    language = filters.get("language")
    if language:
        if language not in _ISO639_3:
            raise ValueError(
                f"news_gdelt: language {language} is not mapped; use one of "
                + ", ".join(sorted(_ISO639_3))
            )
        terms.append(f"sourcelang:{_ISO639_3[language]}")
    recency = filters.get("recency")
    return {
        "query": " ".join(terms),
        "mode": "artlist",
        "format": "json",
        "maxrecords": limit,
        "timespan": _TIMESPAN[recency] if recency else _DEFAULT_TIMESPAN,
    }


class GdeltNewsConnector(Connector):
    id = "news_gdelt"

    def describe(self) -> ConnectorDescription:
        return ConnectorDescription(
            id=self.id,
            name="Dated news by country and language (GDELT; filters country, language, recency)",
            capabilities=["search"],
            supported_filters=["country", "language", "recency"],
            access_limitations=(
                "Shows that publishers reported on a topic and when, with the publisher URL "
                "to fetch with web_fetch; titles only, no article text; not proof of demand "
                "by itself. Default window three months; about 15 minutes behind; small "
                "outlets outside English are thinly covered; no pagination; at most 20 results"
            ),
            cache_ttl_seconds=900,
            minimum_interval_seconds=5.0,
        )

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        params = gdelt_params(query, self.validate_search(query, filters, cursor, limit), limit)
        outcome = get_json(_ENDPOINT, source="GDELT", params=params)
        if outcome.error is not None:
            return outcome.error
        data = outcome.data
        # GDELT answers a query with no matches with an empty object.
        articles = data.get("articles", []) if isinstance(data, dict) else None
        if not isinstance(articles, list):
            return ResearchPage(
                status="error", error_reason="GDELT returned an unexpected response shape"
            )
        documents = []
        for article in articles[:limit]:
            if not isinstance(article, dict):
                continue
            url = article.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
                continue
            title = str(article.get("title") or "")
            documents.append(
                ResearchDocument(
                    connector=self.id,
                    external_id=url,
                    url=url,
                    title=title,
                    text=title,
                    kind="search_snippet",
                    published_at=_seen_at(article.get("seendate")),
                    metadata={
                        "domain": article.get("domain"),
                        "sourcecountry": article.get("sourcecountry"),
                        "language": article.get("language"),
                        "query": query,
                    },
                )
            )
        return ResearchPage(documents=documents, truncated=len(articles) >= limit)
