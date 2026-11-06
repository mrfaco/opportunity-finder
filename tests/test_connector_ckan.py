"""open_data_ckan: portal allowlist, dataset records with resources, fetch forms, typed errors."""

import httpx
import pytest
from django.conf import settings
from django.test import override_settings

from discovery.connectors.base import ConnectorConfigurationError
from discovery.connectors.ckan import CkanConnector, configured_portals
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    provider_fixture,
)

TWO = ["https://datos.gob.ar", "https://www.govdata.de/ckan"]
ONE = ["https://datos.gob.ar"]


def test_default_allowlist_parses_into_four_ckan_portals():
    # Brazil and Spain are absent on purpose: their national portals do not
    # expose CKAN's /api/3/action. Mexico's CKAN is mounted under /busca.
    portals, error = configured_portals()
    assert error is None
    assert portals == {
        "datos.gob.ar": "https://datos.gob.ar",
        "datos.gob.mx": "https://datos.gob.mx/busca",
        "govdata.de": "https://www.govdata.de/ckan",
        "catalog.data.gov": "https://catalog.data.gov",
    }
    assert len(settings.DISCOVERY_CKAN_PORTALS) == 4
    description = CkanConnector().describe()
    assert description.configured and "datos.gob.ar" in description.name


@pytest.mark.parametrize(
    ("portals", "error"),
    [
        ([], "is empty"),
        (["http://datos.gob.ar"], "not an https base URL"),
        (["https://datos.gob.ar", "https://www.datos.gob.ar/"], "twice"),
    ],
)
def test_misconfigured_allowlist_is_a_visible_configuration_error(portals, error):
    with override_settings(DISCOVERY_CKAN_PORTALS=portals):
        description = CkanConnector().describe()
        assert not description.configured and error in description.configuration_error
        with pytest.raises(ConnectorConfigurationError, match=error):
            CkanConnector().search("q")


@override_settings(DISCOVERY_CKAN_PORTALS=TWO)
@pytest.mark.parametrize(
    ("filters", "error"),
    [
        ({}, "give a portal filter, one of datos.gob.ar, govdata.de"),
        ({"portal": "data.gouv.fr"}, "not configured"),
        ({"portal": "datos.gob.ar", "format": "csv"}, "format must be one of"),
    ],
)
def test_portal_is_required_with_two_and_unknown_values_are_refused(filters, error):
    stub = ProviderStub()
    with stub.patch(), pytest.raises(ValueError, match=error):
        CkanConnector().search("monotributo", filters)
    assert stub.requests == []


@override_settings(DISCOVERY_CKAN_PORTALS=ONE)
def test_single_portal_is_implied_and_datasets_carry_resources():
    stub = ProviderStub(json_reply(provider_fixture("ckan_package_search")))
    with stub.patch():
        page = CkanConnector().search("monotributo", {"format": "CSV"}, limit=2)
    request = stub.requests[0]
    assert request["url"] == "https://datos.gob.ar/api/3/action/package_search"
    assert request["params"] == {"q": "monotributo", "rows": 2, "start": 0, "fq": "res_format:CSV"}
    first, second = page.documents
    assert first.kind == "source_record"
    assert first.url == "https://datos.gob.ar/dataset/produccion-padron-monotributistas"
    assert first.external_id == "datos.gob.ar:3f6b1c2e-6f1a-4a43-9b8e-2a1c0e9d1f01"
    assert first.title == "Padrón de monotributistas por actividad"
    assert first.text.startswith("Cantidad de contribuyentes")
    assert first.published_at == "2026-10-05T09:30:00+00:00"
    assert first.metadata["organization"] == "Administración Federal de Ingresos Públicos"
    assert first.metadata["tags"] == ["monotributo", "impuestos"]
    assert first.metadata["resources"] == [
        {
            "name": "Padrón mensual (CSV)",
            "format": "CSV",
            "url": "https://datos.gob.ar/dataset/3f6b1c2e/resource/r1/download/padron.csv",
            "size": 184320,
        },
        {
            "name": "Metodología",
            "format": "PDF",
            "url": "https://datos.gob.ar/dataset/3f6b1c2e/resource/r2/download/metodologia.pdf",
            "size": None,
        },
    ]
    assert second.text == ""
    assert len(second.metadata["resources"]) == 10
    assert second.metadata["resource_count"] == 11
    assert second.metadata["resources"][0]["url"] == "https://datos.gob.ar/c/2016.csv"
    assert page.next_cursor == "2" and page.truncated


@override_settings(DISCOVERY_CKAN_PORTALS=TWO)
def test_cursor_is_the_start_offset_and_must_be_numeric():
    stub = ProviderStub(json_reply(provider_fixture("ckan_package_search")))
    with stub.patch():
        page = CkanConnector().search("q", {"portal": "govdata.de"}, cursor="5", limit=2)
    assert stub.requests[0]["url"] == "https://www.govdata.de/ckan/api/3/action/package_search"
    assert stub.requests[0]["params"]["start"] == 5
    assert page.next_cursor is None and not page.truncated
    assert page.documents[0].url == (
        "https://www.govdata.de/ckan/dataset/produccion-padron-monotributistas"
    )
    with pytest.raises(ValueError, match="cursor must be"):
        CkanConnector().search("q", {"portal": "govdata.de"}, cursor="-1")


@override_settings(DISCOVERY_CKAN_PORTALS=TWO)
@pytest.mark.parametrize(
    ("target", "url", "dataset"),
    [
        (
            "datos.gob.ar:compras-contrataciones-servicios-contables",
            "https://datos.gob.ar/api/3/action/package_show",
            "compras-contrataciones-servicios-contables",
        ),
        (
            "https://datos.gob.ar/dataset/compras-contrataciones-servicios-contables",
            "https://datos.gob.ar/api/3/action/package_show",
            "compras-contrataciones-servicios-contables",
        ),
        (
            "https://www.govdata.de/ckan/dataset/kfz-bestand/",
            "https://www.govdata.de/ckan/api/3/action/package_show",
            "kfz-bestand",
        ),
    ],
)
def test_fetch_by_portal_id_or_dataset_url_returns_all_resources(target, url, dataset):
    stub = ProviderStub(json_reply(provider_fixture("ckan_package_show")))
    with stub.patch():
        page = CkanConnector().fetch(target)
    assert stub.requests[0]["url"] == url and stub.requests[0]["params"] == {"id": dataset}
    (document,) = page.documents
    assert len(document.metadata["resources"]) == 11
    assert document.metadata["resources"][-1]["format"] == "XLSX"
    assert "query" not in document.metadata


@override_settings(DISCOVERY_CKAN_PORTALS=TWO)
@pytest.mark.parametrize(
    "target",
    [
        "https://data.gouv.fr/dataset/x",
        "https://datos.gob.ar/organization/afip",
        "https://www.govdata.de/dataset/x",
        "datos.gob.ar:../../etc",
    ],
)
def test_fetch_refuses_targets_outside_the_allowlist(target):
    with pytest.raises(ValueError, match="fetch requires"):
        CkanConnector().fetch(target)


@override_settings(DISCOVERY_CKAN_PORTALS=ONE)
def test_portal_failures_are_typed_and_name_the_portal():
    with ProviderStub(httpx.Response(503)).patch():
        page = CkanConnector().search("q")
    assert page.status == "error"
    assert page.error_reason == "CKAN portal datos.gob.ar returned HTTP 503"
    unsuccessful = {"success": False, "error": {"__type": "Search Query Error"}}
    with ProviderStub(json_reply(unsuccessful)).patch():
        page = CkanConnector().search("q")
    assert page.status == "error" and "datos.gob.ar reported an unsuccessful" in page.error_reason
    with ProviderStub(json_reply({"success": True, "result": {"results": []}})).patch():
        assert "unexpected shape" in CkanConnector().search("q").error_reason
    not_found = {"success": False, "error": {"__type": "Not Found Error"}}
    with ProviderStub(json_reply(not_found, status=404)).patch():
        assert CkanConnector().fetch("datos.gob.ar:missing").status == "not_found"
    assert_typed_failures(lambda: CkanConnector().search("q"))
    assert_typed_failures(lambda: CkanConnector().fetch("datos.gob.ar:x"))
