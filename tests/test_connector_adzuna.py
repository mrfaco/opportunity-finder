"""jobs_adzuna: market enforcement, request mapping, adverts typed job_post, typed failures."""

import httpx
import pytest
from django.test import override_settings

from discovery.connectors.adzuna import AdzunaJobsConnector
from discovery.connectors.base import ConnectorConfigurationError, UnsupportedCapabilityError
from discovery.research import perform_research
from discovery.source_types import classify
from tests.discovery_fixtures import (
    ProviderStub,
    assert_typed_failures,
    json_reply,
    provider_fixture,
    research_task,
)

KEYED = {"ADZUNA_APP_ID": "adz-id", "ADZUNA_APP_KEY": "adz-key"}
LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "adzuna"}
}


def _search(stub, *args, **kwargs):
    with stub.patch():
        return AdzunaJobsConnector().search(*args, **kwargs)


def test_unconfigured_adzuna_is_refused_before_any_request():
    assert not AdzunaJobsConnector().describe().configured
    with (
        override_settings(ADZUNA_APP_ID="only-id"),
        pytest.raises(ConnectorConfigurationError, match="ADZUNA_APP_ID and ADZUNA_APP_KEY"),
    ):
        _search(ProviderStub(), "contador", {"country": "mx"})


@override_settings(**KEYED)
@pytest.mark.parametrize(
    ("filters", "error"),
    [
        ({}, "country filter is required"),
        ({"country": "ar"}, "does not cover ar; use jobs_jooble for Argentina"),
        ({"country": "jp"}, "does not cover jp; markets are"),
        ({"country": "mx", "location": ""}, "location must be"),
        ({"country": "mx", "subreddit": "x"}, "unsupported filters"),
    ],
)
def test_market_list_and_filters_are_enforced_before_any_request(filters, error):
    stub = ProviderStub()
    with pytest.raises((ValueError, UnsupportedCapabilityError), match=error):
        _search(stub, "contador", filters)
    assert stub.requests == []


@override_settings(**KEYED)
def test_request_mapping_and_page_cursor():
    stub = ProviderStub(json_reply(provider_fixture("adzuna_search")))
    page = _search(
        stub, "contador", {"country": "mx", "location": "Monterrey", "recency": "month"}, "2", 3
    )
    request = stub.requests[0]
    assert request["url"] == "https://api.adzuna.com/v1/api/jobs/mx/search/2"
    assert request["params"] == {
        "app_id": "adz-id",
        "app_key": "adz-key",
        "what": "contador",
        "results_per_page": 3,
        "sort_by": "date",
        "where": "Monterrey",
        "max_days_old": 30,
    }
    assert page.next_cursor == "3" and page.truncated
    with pytest.raises(ValueError, match="cursor must be a page number"):
        _search(ProviderStub(), "q", {"country": "mx"}, "0")


@override_settings(**KEYED)
def test_adverts_are_job_post_records_with_salary_metadata():
    page = _search(
        ProviderStub(json_reply(provider_fixture("adzuna_search"))), "contador", {"country": "mx"}
    )
    assert len(page.documents) == 2
    advert = page.documents[0]
    assert advert.kind == "source_record"
    assert advert.title == "Contador Público para despacho"
    assert "<strong>" not in advert.text and advert.text.startswith("Despacho contable")
    assert advert.author == "Despacho Garza y Asociados"
    assert advert.published_at == "2026-10-30T08:12:44+00:00"
    assert advert.url.startswith("https://www.adzuna.com.mx/land/ad/4912345678")
    assert advert.external_id == "mx:4912345678"
    assert advert.metadata == {
        "adzuna_id": "4912345678",
        "country": "mx",
        "company": "Despacho Garza y Asociados",
        "location": "Monterrey, Nuevo León",
        "salary_min": 18000,
        "salary_max": 25000,
        "salary_is_predicted": False,
        "currency": "MXN",
        "contract_time": "full_time",
        "contract_type": "permanent",
        "category": "Empleos de Contabilidad y Finanzas",
        "query": "contador",
    }
    assert page.documents[1].metadata["salary_is_predicted"] is True
    typed = classify(advert.connector, advert.kind, advert.url)
    assert typed.source_type == "job_post" and typed.rule == "host_pattern:adzuna.com.*"
    description = AdzunaJobsConnector().describe()
    assert "adz-key" not in description.model_dump_json() and "jobs_jooble" in (
        description.access_limitations
    )


@override_settings(**KEYED)
def test_bad_key_and_other_failures_are_typed():
    page = _search(ProviderStub(httpx.Response(403)), "q", {"country": "mx"})
    assert page.status == "access_denied" and page.error_reason.startswith("Adzuna")
    assert "adz-key" not in page.error_reason
    assert_typed_failures(lambda: AdzunaJobsConnector().search("q", {"country": "gb"}))
    bad = _search(ProviderStub(json_reply({"results": "x"})), "q", {"country": "mx"})
    assert bad.status == "error"


@pytest.mark.django_db
@override_settings(**KEYED, CACHES=LOCMEM)
def test_research_records_adverts_as_job_post_evidence():
    task = research_task(connectors=["search_auto", "web_fetch", "jobs_adzuna"])
    with ProviderStub(json_reply(provider_fixture("adzuna_search"))).patch():
        perform_research(
            task,
            "adzuna-1",
            {
                "connector": "jobs_adzuna",
                "action": "search",
                "query": "contador",
                "filters": {"country": "mx", "location": "Monterrey"},
            },
        )
    rows = task.discovery.evidence.all()
    assert rows.count() == 2 and {row.source_type for row in rows} == {"job_post"}
    assert all("adz-key" not in str(row.metadata) for row in rows)
    operation = task.operations.get(operation_key="adzuna-1")
    assert "adz-key" not in str(operation.request) and "adz-key" not in str(operation.response)
