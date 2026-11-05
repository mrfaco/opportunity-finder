"""Deterministic evidence source typing from versioned rules kept in git.

Classification uses only the connector, document kind and URL. It never asks a
model, so the roles cannot grade their own evidence. Connectors stay unaware of
source types; the research service classifies at write time.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RULES_PATH = Path(__file__).with_name("source_type_rules.json")

SourceType = Literal[
    "search_snippet",
    "forum",
    "review",
    "job_post",
    "tender",
    "marketplace_listing",
    "regulator",
    "statistics",
    "news",
    "vendor",
    "unclassified",
]
# First match wins. Statistics precedes regulator so a government statistics
# office (indec.gob.ar) is statistics, as the specification requires; every
# other position follows the specification's table.
CLASSIFICATION_ORDER: tuple[SourceType, ...] = (
    "search_snippet",
    "forum",
    "review",
    "job_post",
    "tender",
    "marketplace_listing",
    "statistics",
    "regulator",
    "news",
    "vendor",
)
UNCLASSIFIED: SourceType = "unclassified"


class HostPath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1)
    path_prefix: str = Field(pattern=r"^/[a-z0-9/_-]+$")


class SourceTypeList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1)
    kinds: list[str] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    host_paths: list[HostPath] = Field(default_factory=list)
    host_patterns: list[str] = Field(default_factory=list)
    path_segments: list[str] = Field(default_factory=list)

    @field_validator("kinds", "connectors", "domains", "host_patterns", "path_segments")
    @classmethod
    def sorted_lowercase_unique(cls, values: list[str]) -> list[str]:
        if any(value != value.strip().lower() or not value for value in values):
            raise ValueError("Rule entries must be non-empty, lower case and trimmed")
        if values != sorted(set(values)):
            raise ValueError("Rule entries must be sorted and unique")
        return values


class SourceTypeRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    rules_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    types: dict[SourceType, SourceTypeList]

    @model_validator(mode="after")
    def every_ordered_type(self) -> SourceTypeRules:
        if set(self.types) != set(CLASSIFICATION_ORDER):
            raise ValueError("Rules must define exactly the ordered source types")
        return self


class SourceTypeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: SourceType
    rule: str
    rules_version: str


@functools.cache
def load_rules() -> SourceTypeRules:
    return SourceTypeRules.model_validate_json(RULES_PATH.read_text(encoding="utf-8"))


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _pattern_matches(host: str, pattern: str) -> bool:
    labels = host.split(".")
    wanted = pattern.split(".")
    if len(labels) < len(wanted):
        return False
    return all(
        expected in {"*", actual}
        for expected, actual in zip(wanted, labels[-len(wanted) :], strict=True)
    )


def _match(rules: SourceTypeList, connector: str, kind: str, host: str, path: str) -> str | None:
    if kind in rules.kinds:
        return f"kind:{kind}"
    if connector in rules.connectors:
        return f"connector:{connector}"
    for domain in rules.domains:
        if _host_matches(host, domain):
            return f"domain:{domain}"
    for entry in rules.host_paths:
        if _host_matches(host, entry.domain) and (
            path == entry.path_prefix or path.startswith(entry.path_prefix + "/")
        ):
            return f"host_path:{entry.domain}{entry.path_prefix}"
    for pattern in rules.host_patterns:
        if _pattern_matches(host, pattern):
            return f"host_pattern:{pattern}"
    segments = {segment for segment in re.split(r"/+", path) if segment}
    for segment in rules.path_segments:
        if segment in segments:
            return f"path_segment:{segment}"
    return None


def classify(connector: str, kind: str, url: str) -> SourceTypeResult:
    rules = load_rules()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    path = (parsed.path or "/").lower()
    for source_type in CLASSIFICATION_ORDER:
        rule = _match(rules.types[source_type], connector, kind, host, path)
        if rule is not None:
            return SourceTypeResult(
                source_type=source_type, rule=rule, rules_version=rules.rules_version
            )
    return SourceTypeResult(
        source_type=UNCLASSIFIED, rule="default", rules_version=rules.rules_version
    )
