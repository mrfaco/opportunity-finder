"""Read-only, versioned research capabilities independent of the agent loop."""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def parse_stated_date(value: str, *, now: datetime | None = None) -> str | None:
    """A source-stated date as a UTC ISO timestamp, or None when it cannot be trusted.

    Accepts ISO-8601 or RFC-2822 only. Dates more than one day in the future
    are rejected: a wrong date is worse than none. A date without a zone is
    read as UTC.
    """
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:  # allow: suppress-exception — not ISO-8601; try RFC-2822 next
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):  # allow: suppress-exception — unparseable source date
            # Untrusted page metadata; the caller records no date rather than a guess.
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed > (now or datetime.now(UTC)) + timedelta(days=1):
        return None
    return parsed.astimezone(UTC).isoformat()


# Market and period filters understood by the free search connectors.
RECENCY_VALUES = ("day", "week", "month", "year")
_ISO_CODE = re.compile(r"^[a-z]{2}$")


def validate_market_filters(connector_id: str, supplied: dict[str, Any]) -> None:
    """Reject country/language/recency values outside their allowed forms."""
    if "country" in supplied and not (
        isinstance(supplied["country"], str) and _ISO_CODE.match(supplied["country"])
    ):
        raise ValueError(f"{connector_id}: country must be a lower-case ISO 3166-1 alpha-2 code")
    if "language" in supplied and not (
        isinstance(supplied["language"], str) and _ISO_CODE.match(supplied["language"])
    ):
        raise ValueError(f"{connector_id}: language must be a lower-case ISO 639-1 code")
    if "recency" in supplied and supplied["recency"] not in RECENCY_VALUES:
        raise ValueError(f"{connector_id}: recency must be one of {', '.join(RECENCY_VALUES)}")


class ConnectorError(RuntimeError):
    """A research capability failed without producing usable evidence."""


class ConnectorConfigurationError(ConnectorError):
    """An explicitly selected connector cannot run with current settings."""


class UnsupportedCapabilityError(ConnectorError):
    """The requested operation/filter is not supported by this connector."""


class ResearchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    connector: str
    connector_version: str = "1.0"
    external_id: str
    url: str
    title: str = ""
    text: str = ""
    kind: Literal["search_snippet", "fetched_page", "source_record", "internal_record"]
    author: str | None = None
    published_at: str | None = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def hash_content(self) -> ResearchDocument:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.content_hash and self.content_hash != digest:
            raise ValueError("content_hash does not match the captured text")
        self.content_hash = digest
        return self


class ResearchPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "not_found", "access_denied", "rate_limited", "timeout", "error"] = (
        "success"
    )
    documents: list[ResearchDocument] = Field(default_factory=list)
    next_cursor: str | None = None
    truncated: bool = False
    cost_usd: Decimal = Decimal("0")
    cost_is_estimate: bool = False
    error_reason: str | None = None
    retry_after_seconds: int | None = None


class ConnectorDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    id: str
    version: str = "1.0"
    name: str
    capabilities: list[str]
    supported_filters: list[str] = Field(default_factory=list)
    configuration_requirements: list[str] = Field(default_factory=list)
    configured: bool = True
    configuration_error: str | None = None
    access_limitations: str = ""
    search_cost_upper_bound_usd: Decimal = Decimal("0")
    fetch_cost_upper_bound_usd: Decimal = Decimal("0")
    cache_ttl_seconds: int = 3600
    minimum_interval_seconds: float = 1.0


class Connector(ABC):
    id: str
    version = "1.0"

    @abstractmethod
    def describe(self) -> ConnectorDescription:
        raise NotImplementedError

    def check_configuration(self) -> None:
        description = self.describe()
        if not description.configured:
            raise ConnectorConfigurationError(
                description.configuration_error or f"Connector {self.id} is not configured"
            )

    def validate_search(
        self,
        query: str,
        filters: dict[str, Any] | None,
        cursor: str | None,
        limit: int,
        *,
        pagination: bool = False,
    ) -> dict[str, Any]:
        self.check_configuration()
        if not query.strip():
            raise ValueError("A search query must not be empty")
        if not 1 <= limit <= 20:
            raise ValueError("Search limit must be between 1 and 20")
        supplied = filters or {}
        unknown = set(supplied) - set(self.describe().supported_filters)
        if unknown:
            raise UnsupportedCapabilityError(f"{self.id}: unsupported filters {sorted(unknown)}")
        validate_market_filters(self.id, supplied)
        if cursor is not None and not pagination:
            raise UnsupportedCapabilityError(f"{self.id}: pagination is not supported")
        return supplied

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        raise UnsupportedCapabilityError(f"{self.id} does not support search")

    def fetch(self, target: str) -> ResearchPage:
        raise UnsupportedCapabilityError(f"{self.id} does not support fetch")
