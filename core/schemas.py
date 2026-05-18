"""Pydantic base schemas with JSON Schema export.

Every structured artifact in the system carries a ``schema_version`` field so
migrations between versions are explicit. Models inherit ``VersionedSchema``
to get a default version they can override.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VersionedSchema(BaseModel):
    """Base for structured artifacts with explicit schema versions."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(default="1.0")

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return cls.model_json_schema()
