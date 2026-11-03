"""Read-only, versioned research capabilities independent of the agent loop."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    status: Literal["success", "not_found", "access_denied", "rate_limited"] = "success"
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
