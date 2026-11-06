"""Government datasets from an allowlist of CKAN open-data portals."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from discovery.connectors.base import (
    Connector,
    ConnectorDescription,
    ResearchDocument,
    ResearchPage,
    parse_stated_date,
)
from discovery.connectors.http import get_json

_FORMATS = ("CSV", "JSON", "XLSX", "PDF")
_NOTES_LIMIT = 4000
_SEARCH_RESOURCES = 10
_DATASET_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def configured_portals() -> tuple[dict[str, str], str | None]:
    """Portal host (without ``www.``) -> API base URL, and the first configuration error."""
    portals: dict[str, str] = {}
    for entry in getattr(settings, "DISCOVERY_CKAN_PORTALS", []):
        parsed = urlsplit(str(entry).strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            return {}, f"DISCOVERY_CKAN_PORTALS entry {entry!r} is not an https base URL"
        host = parsed.hostname.lower().removeprefix("www.")
        if host in portals:
            return {}, f"DISCOVERY_CKAN_PORTALS lists {host} twice"
        portals[host] = f"https://{parsed.hostname.lower()}{parsed.path.rstrip('/')}"
    if not portals:
        return {}, "DISCOVERY_CKAN_PORTALS is empty"
    return portals, None


def _resource(item: dict[str, Any]) -> dict[str, Any] | None:
    url = item.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        return None
    size = item.get("size")
    return {
        "name": str(item.get("name") or ""),
        "format": str(item.get("format") or "").upper(),
        "url": url,
        "size": size if isinstance(size, int) else None,
    }


class CkanConnector(Connector):
    id = "open_data_ckan"

    def describe(self) -> ConnectorDescription:
        portals, error = configured_portals()
        listed = ", ".join(portals) or "none"
        return ConnectorDescription(
            id=self.id,
            name=f"Government open-data catalogues (CKAN: {listed}; filters portal, format)",
            capabilities=["search", "fetch"],
            supported_filters=["portal", "format"],
            configuration_requirements=["DISCOVERY_CKAN_PORTALS"],
            configured=error is None,
            configuration_error=error,
            access_limitations=(
                "Shows where official statistics, contracts and registries are published and "
                "links their files (CSV/PDF readable with web_fetch); a dataset is not an "
                "answer until its file is read. portal is required when more than one is "
                "configured; at most 20 datasets per page (cursor); ten resources per search "
                "result, all on fetch"
            ),
            cache_ttl_seconds=86400,
            minimum_interval_seconds=1.0,
        )

    def _portal(self, supplied: dict[str, Any]) -> tuple[str, str]:
        portals, _ = configured_portals()
        portal = supplied.get("portal")
        if portal is None:
            if len(portals) > 1:
                raise ValueError(
                    "open_data_ckan: give a portal filter, one of " + ", ".join(sorted(portals))
                )
            portal = next(iter(portals))
        if portal not in portals:
            raise ValueError(
                f"open_data_ckan: portal {portal!r} is not configured; use one of "
                + ", ".join(sorted(portals))
            )
        return portal, portals[portal]

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 5,
    ) -> ResearchPage:
        supplied = self.validate_search(query, filters, cursor, limit, pagination=True)
        portal, base = self._portal(supplied)
        if cursor is not None and not cursor.isdigit():
            raise ValueError("open_data_ckan: cursor must be a nonnegative integer")
        start = int(cursor or "0")
        params: dict[str, Any] = {"q": query, "rows": limit, "start": start}
        if "format" in supplied:
            if supplied["format"] not in _FORMATS:
                raise ValueError(f"open_data_ckan: format must be one of {', '.join(_FORMATS)}")
            params["fq"] = f"res_format:{supplied['format']}"
        result = self._call(portal, f"{base}/api/3/action/package_search", params)
        if isinstance(result, ResearchPage):
            return result
        datasets, count = result.get("results"), result.get("count")
        if not isinstance(datasets, list) or not isinstance(count, int):
            return self._shape_error(portal)
        documents = [
            self._dataset(portal, base, item, _SEARCH_RESOURCES, query)
            for item in datasets[:limit]
            if isinstance(item, dict)
        ]
        if any(document is None for document in documents):
            return self._shape_error(portal)
        more = start + len(datasets) < count
        return ResearchPage(
            documents=[document for document in documents if document is not None],
            next_cursor=str(start + len(datasets)) if more and datasets else None,
            truncated=more,
        )

    def fetch(self, target: str) -> ResearchPage:
        self.check_configuration()
        portals, _ = configured_portals()
        portal, _, dataset = target.partition(":")
        if portal in portals and _DATASET_ID.match(dataset):
            return self._show(portal, dataset)
        parsed = urlsplit(target)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme == "https" and host in portals:
            base_path = urlsplit(portals[host]).path
            match = re.fullmatch(
                re.escape(base_path) + r"/dataset/([A-Za-z0-9_-]{1,200})/?", parsed.path
            )
            if match:
                return self._show(host, match[1])
        raise ValueError(
            "open_data_ckan fetch requires <portal>:<dataset id> or a configured "
            "portal's /dataset/<id> URL"
        )

    def _show(self, portal: str, dataset: str) -> ResearchPage:
        base = configured_portals()[0][portal]
        result = self._call(portal, f"{base}/api/3/action/package_show", {"id": dataset})
        if isinstance(result, ResearchPage):
            return result
        document = self._dataset(portal, base, result, None, None)
        return ResearchPage(documents=[document]) if document else self._shape_error(portal)

    @staticmethod
    def _call(portal: str, url: str, params: dict[str, Any]) -> dict[str, Any] | ResearchPage:
        outcome = get_json(url, source=f"CKAN portal {portal}", params=params)
        if outcome.error is not None:
            return outcome.error
        data = outcome.data
        if not isinstance(data, dict) or data.get("success") is not True:
            return ResearchPage(
                status="error", error_reason=f"CKAN portal {portal} reported an unsuccessful call"
            )
        result = data.get("result")
        return result if isinstance(result, dict) else CkanConnector._shape_error(portal)

    @staticmethod
    def _shape_error(portal: str) -> ResearchPage:
        return ResearchPage(
            status="error", error_reason=f"CKAN portal {portal} returned an unexpected shape"
        )

    def _dataset(
        self,
        portal: str,
        base: str,
        item: dict[str, Any],
        resource_limit: int | None,
        query: str | None,
    ) -> ResearchDocument | None:
        name, dataset_id = item.get("name"), item.get("id")
        if not isinstance(name, str) or not isinstance(dataset_id, str):
            return None
        raw = item.get("resources")
        raw = raw if isinstance(raw, list) else []
        resources = [r for r in (_resource(x) for x in raw if isinstance(x, dict)) if r]
        organization = item.get("organization")
        tags = item.get("tags")
        tags = tags if isinstance(tags, list) else []
        modified = item.get("metadata_modified")
        return ResearchDocument(
            connector=self.id,
            external_id=f"{portal}:{dataset_id}",
            url=f"{base}/dataset/{name}",
            title=str(item.get("title") or name),
            text=str(item.get("notes") or "")[:_NOTES_LIMIT],
            kind="source_record",
            published_at=parse_stated_date(modified) if isinstance(modified, str) else None,
            metadata={
                "portal": portal,
                "dataset_id": dataset_id,
                "name": name,
                "organization": organization.get("title")
                if isinstance(organization, dict)
                else None,
                "tags": [t["name"] for t in tags if isinstance(t, dict) and "name" in t],
                "resources": resources[:resource_limit] if resource_limit else resources,
                "resource_count": len(resources),
                **({"query": query} if query is not None else {}),
            },
        )
