"""Job advertisements with salary bands from the Adzuna API (keys required, opt-in)."""

from __future__ import annotations

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

# Adzuna's markets and the currency its salaries are quoted in (the API returns
# amounts without a currency; each country site quotes its own). Sorted by code.
_MARKET_CURRENCY = {
    "at": "EUR",
    "au": "AUD",
    "be": "EUR",
    "br": "BRL",
    "ca": "CAD",
    "ch": "CHF",
    "de": "EUR",
    "es": "EUR",
    "fr": "EUR",
    "gb": "GBP",
    "in": "INR",
    "it": "EUR",
    "mx": "MXN",
    "nl": "EUR",
    "nz": "NZD",
    "pl": "PLN",
    "sg": "SGD",
    "us": "USD",
    "za": "ZAR",
}
_MAX_DAYS_OLD = {"day": 1, "week": 7, "month": 30, "year": 365}
_DESCRIPTION_LIMIT = 3000


class AdzunaJobsConnector(Connector):
    id = "jobs_adzuna"

    def describe(self) -> ConnectorDescription:
        configured = bool(getattr(settings, "ADZUNA_APP_ID", "")) and bool(
            getattr(settings, "ADZUNA_APP_KEY", "")
        )
        return ConnectorDescription(
            id=self.id,
            name="Job advertisements with salaries (Adzuna; filters country, location, recency)",
            capabilities=["search"],
            supported_filters=["country", "location", "recency"],
            configuration_requirements=["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
            configured=configured,
            configuration_error=(
                None if configured else "ADZUNA_APP_ID and ADZUNA_APP_KEY are not configured"
            ),
            access_limitations=(
                "Shows employers paying for this work now, often with a salary band; not "
                "that customers buy the service. country is required, one of "
                + " ".join(_MARKET_CURRENCY)
                + " (no Argentina: jobs_jooble covers it); at most 20 adverts per page "
                "(cursor is the page number); descriptions are Adzuna's excerpts"
            ),
            cache_ttl_seconds=21600,
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
        country = supplied.get("country")
        if country is None:
            raise ValueError("jobs_adzuna: the country filter is required")
        if country not in _MARKET_CURRENCY:
            pointer = "; use jobs_jooble for Argentina" if country == "ar" else ""
            raise ValueError(
                f"jobs_adzuna: Adzuna does not cover {country}{pointer}; markets are "
                + " ".join(_MARKET_CURRENCY)
            )
        location = supplied.get("location")
        if location is not None and not (isinstance(location, str) and 0 < len(location) <= 100):
            raise ValueError("jobs_adzuna: location must be a place name up to 100 characters")
        if cursor is not None and not (cursor.isdigit() and int(cursor) >= 1):
            raise ValueError("jobs_adzuna: cursor must be a page number from 1")
        page_number = int(cursor or "1")
        params: dict[str, Any] = {
            "app_id": settings.ADZUNA_APP_ID,
            "app_key": settings.ADZUNA_APP_KEY,
            "what": query,
            "results_per_page": limit,
            "sort_by": "date",
        }
        if location:
            params["where"] = location
        if supplied.get("recency"):
            params["max_days_old"] = _MAX_DAYS_OLD[supplied["recency"]]
        outcome = get_json(
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page_number}",
            source="Adzuna",
            params=params,
        )
        if outcome.error is not None:
            return outcome.error
        data = outcome.data
        results = data.get("results") if isinstance(data, dict) else None
        count = data.get("count") if isinstance(data, dict) else None
        if not isinstance(results, list) or not isinstance(count, int):
            return ResearchPage(
                status="error", error_reason="Adzuna returned an unexpected response shape"
            )
        documents = [
            document
            for document in (self._advert(item, country, query) for item in results[:limit])
            if document is not None
        ]
        more = page_number * limit < count
        return ResearchPage(
            documents=documents,
            next_cursor=str(page_number + 1) if more else None,
            truncated=more,
        )

    def _advert(self, item: Any, country: str, query: str) -> ResearchDocument | None:
        if not isinstance(item, dict):
            return None
        url = item.get("redirect_url")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
            return None
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        created = item.get("created")
        employer = company.get("display_name") if company else None
        return ResearchDocument(
            connector=self.id,
            external_id=f"{country}:{item.get('id')}",
            url=url,
            title=html_to_text(str(item.get("title") or "Job advertisement")),
            text=html_to_text(str(item.get("description") or ""))[:_DESCRIPTION_LIMIT],
            kind="source_record",
            author=employer if isinstance(employer, str) else None,
            published_at=parse_stated_date(created) if isinstance(created, str) else None,
            metadata={
                "adzuna_id": item.get("id"),
                "country": country,
                "company": employer,
                "location": location.get("display_name") if location else None,
                "salary_min": item.get("salary_min"),
                "salary_max": item.get("salary_max"),
                "salary_is_predicted": item.get("salary_is_predicted") == "1",
                "currency": _MARKET_CURRENCY[country],
                "contract_time": item.get("contract_time"),
                "contract_type": item.get("contract_type"),
                "category": category.get("label") if category else None,
                "query": query,
            },
        )
