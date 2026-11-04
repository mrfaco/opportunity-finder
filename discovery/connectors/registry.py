"""Explicit allowlist of research connector implementations."""

from __future__ import annotations

from collections.abc import Sequence

from django.conf import settings
from django.utils.module_loading import import_string

from discovery.connectors.base import Connector, ConnectorConfigurationError
from discovery.connectors.search import AutoSearchConnector, BingConnector
from discovery.connectors.sources import (
    GitHubConnector,
    HackerNewsConnector,
    LocalCorpusConnector,
    StackExchangeConnector,
)
from discovery.connectors.web import DuckDuckGoConnector, TavilyConnector, WebFetchConnector

_BUILTINS: tuple[type[Connector], ...] = (
    TavilyConnector,
    AutoSearchConnector,
    BingConnector,
    WebFetchConnector,
    DuckDuckGoConnector,
    HackerNewsConnector,
    GitHubConnector,
    StackExchangeConnector,
    LocalCorpusConnector,
)


def _registry() -> dict[str, Connector]:
    registry: dict[str, Connector] = {cls.id: cls() for cls in _BUILTINS}
    paths = getattr(settings, "DISCOVERY_CONNECTOR_CLASSES", [])
    if not isinstance(paths, (list, tuple)):
        raise ConnectorConfigurationError(
            "DISCOVERY_CONNECTOR_CLASSES must be a list of class paths"
        )
    for path in paths:
        implementation = import_string(path)
        if not isinstance(implementation, type) or not issubclass(implementation, Connector):
            raise ConnectorConfigurationError(
                f"Configured connector {path} is not a Connector class"
            )
        connector = implementation()
        if connector.id in registry:
            raise ConnectorConfigurationError(f"Duplicate connector ID: {connector.id}")
        registry[connector.id] = connector
    return registry


def get_connector(connector_id: str) -> Connector:
    registry = _registry()
    if connector_id not in registry:
        raise ConnectorConfigurationError(f"Unknown connector ID: {connector_id}")
    return registry[connector_id]


def list_connectors() -> list[Connector]:
    return list(_registry().values())


def describe_connectors() -> list[dict]:
    return [connector.describe().model_dump(mode="json") for connector in list_connectors()]


def validate_connectors(connector_ids: Sequence[str]) -> None:
    if not connector_ids:
        raise ConnectorConfigurationError("Select at least one research connector")
    if len(set(connector_ids)) != len(connector_ids):
        raise ConnectorConfigurationError("Connector selection contains duplicate IDs")
    for connector_id in connector_ids:
        get_connector(connector_id).check_configuration()
    if not any("search" in get_connector(item).describe().capabilities for item in connector_ids):
        raise ConnectorConfigurationError("Select at least one connector with search capability")
