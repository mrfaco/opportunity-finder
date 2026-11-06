"""Deterministic source typing: rules file, ordering, and the research write path."""

import json
from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings

from discovery.agent_tools import AgentTools
from discovery.connectors.base import ResearchDocument, ResearchPage
from discovery.loop import _initial_context
from discovery.research import perform_research
from discovery.source_types import (
    CLASSIFICATION_ORDER,
    RULES_PATH,
    SourceTypeRules,
    classify,
    load_rules,
)
from tests.discovery_fixtures import research_task

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-types-test",
    }
}


@pytest.mark.parametrize(
    ("connector", "kind", "url", "expected", "rule"),
    [
        ("bing", "search_snippet", "https://acme.com/pricing", "search_snippet", "kind:"),
        ("hacker_news", "source_record", "https://news.ycombinator.com/item?id=1", "forum", ""),
        ("web_fetch", "fetched_page", "https://www.reddit.com/r/smallbusiness/", "forum", ""),
        ("web_fetch", "fetched_page", "https://www.trustpilot.com/review/acme.com", "review", ""),
        ("web_fetch", "fetched_page", "https://www.google.com/maps/place/x", "review", ""),
        ("web_fetch", "fetched_page", "https://www.linkedin.com/jobs/view/1", "job_post", ""),
        ("web_fetch", "fetched_page", "https://www.workana.com/job/x", "job_post", ""),
        ("jobs_adzuna", "source_record", "https://www.adzuna.de/land/ad/1", "job_post", ""),
        ("jobs_adzuna", "source_record", "https://www.adzuna.co.uk/land/ad/1", "job_post", ""),
        ("jobs_adzuna", "source_record", "https://www.adzuna.com.mx/land/ad/1", "job_post", ""),
        ("web_fetch", "fetched_page", "https://jooble.org/desc/1", "job_post", "domain:"),
        ("wayback", "source_record", "https://web.archive.org/web/2020/x", "unclassified", ""),
        ("web_fetch", "fetched_page", "https://comprar.gob.ar/proceso/1", "tender", ""),
        ("web_fetch", "fetched_page", "https://ted.europa.eu/notice/1", "tender", ""),
        ("web_fetch", "fetched_page", "https://sam.gov/opp/1", "tender", ""),
        (
            "web_fetch",
            "fetched_page",
            "https://articulo.mercadolibre.com.ar/MLA-1",
            "marketplace_listing",
            "",
        ),
        ("web_fetch", "fetched_page", "https://www.indec.gob.ar/indec/web/x", "statistics", ""),
        ("web_fetch", "fetched_page", "https://ec.europa.eu/eurostat/data", "statistics", ""),
        ("web_fetch", "fetched_page", "https://www.bcra.gob.ar/x", "regulator", ""),
        ("web_fetch", "fetched_page", "https://www.ftc.gov/rules", "regulator", ""),
        ("web_fetch", "fetched_page", "https://www.gov.br/receita", "regulator", ""),
        ("web_fetch", "fetched_page", "https://ec.europa.eu/info/law", "regulator", ""),
        ("web_fetch", "fetched_page", "https://www.lanacion.com.ar/economia/x", "news", ""),
        ("web_fetch", "fetched_page", "https://acme.com/pricing", "vendor", "path_segment:"),
        ("web_fetch", "fetched_page", "https://acme.com/es/precios/", "vendor", ""),
        ("web_fetch", "fetched_page", "https://acme.com/", "unclassified", "default"),
        ("web_fetch", "fetched_page", "https://acme.com/about", "unclassified", "default"),
        ("web_fetch", "fetched_page", "https://www.army.mil/x", "unclassified", "default"),
    ],
)
def test_classify_one_example_per_type(connector, kind, url, expected, rule):
    result = classify(connector, kind, url)
    assert result.source_type == expected
    assert result.rule.startswith(rule)
    assert result.rules_version == load_rules().rules_version


def test_ordering_first_match_wins():
    # A .gob.ar procurement portal is a tender, not a regulator.
    assert classify("web_fetch", "fetched_page", "https://comprar.gob.ar/x").source_type == (
        "tender"
    )
    # A government statistics office is statistics, not a regulator.
    assert classify("web_fetch", "fetched_page", "https://ibge.gov.br/x").source_type == (
        "statistics"
    )
    # A snippet is a snippet even when it points at an otherwise typed page.
    assert classify("duckduckgo", "search_snippet", "https://www.reddit.com/r/x").rule == (
        "kind:search_snippet"
    )
    # A news site's pricing page is still news; vendor is last and path-only.
    assert classify("web_fetch", "fetched_page", "https://www.ft.com/products").source_type == (
        "news"
    )
    assert CLASSIFICATION_ORDER[-1] == "vendor"


def test_rules_file_validates_and_every_listed_entry_classifies_to_its_own_list():
    rules = SourceTypeRules.model_validate_json(RULES_PATH.read_text())
    for name, entries in rules.types.items():
        assert entries.purpose
        for domain in entries.domains:
            result = classify("web_fetch", "fetched_page", f"https://{domain}/x")
            assert result.source_type == name, (domain, result)
        for entry in entries.host_paths:
            url = f"https://{entry.domain}{entry.path_prefix}/x"
            assert classify("web_fetch", "fetched_page", url).source_type == name
        for segment in entries.path_segments:
            url = f"https://seller.example/{segment}"
            assert classify("web_fetch", "fetched_page", url).source_type == name


def test_rules_contract_rejects_unsorted_lists():
    raw = json.loads(RULES_PATH.read_text())
    raw["types"]["news"]["domains"] = ["wsj.com", "ft.com"]
    with pytest.raises(ValueError, match="sorted"):
        SourceTypeRules.model_validate(raw)
    raw = json.loads(RULES_PATH.read_text())
    del raw["types"]["vendor"]
    with pytest.raises(ValueError, match="ordered source types"):
        SourceTypeRules.model_validate(raw)


@pytest.mark.django_db
@override_settings(CACHES=LOCMEM)
def test_research_path_stamps_type_and_metadata_and_shows_them_to_roles():
    task = research_task()
    page = ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                external_id="pricing",
                url="https://acme.com/pricing",
                text="Plans from USD 40",
                kind="fetched_page",
            )
        ]
    )
    with patch("discovery.connectors.web.fetch_public_url", return_value=page):
        result = perform_research(
            task,
            "fetch",
            {"connector": "web_fetch", "action": "fetch", "target": "https://acme.com/pricing"},
        )
    rss = (
        "<rss><channel><item><title>T</title><link>https://www.reddit.com/r/x</link>"
        "<description>D</description></item></channel></rss>"
    )
    replies = [httpx.Response(202), httpx.Response(200, text=rss)]
    with patch("discovery.connectors.web.httpx.get", side_effect=replies):
        snippet = perform_research(
            task,
            "search",
            {"connector": "search_auto", "action": "search", "query": "acme pricing"},
        )
    vendor = task.discovery.evidence.get(url="https://acme.com/pricing")
    assert vendor.source_type == "vendor"
    assert vendor.schema_version == "1.1"
    assert vendor.metadata["source_type_rule"] == "path_segment:pricing"
    assert vendor.metadata["source_type_rules_version"] == load_rules().rules_version
    found = task.discovery.evidence.get(url="https://www.reddit.com/r/x")
    assert found.source_type == "search_snippet"
    rendered = json.loads(AgentTools.model_result(result))
    assert rendered["documents"][0]["source_type"] == "vendor"
    assert rendered["documents"][0]["origin"] == "acme.com"
    assert "published_at" in rendered["documents"][0]
    assert json.loads(AgentTools.model_result(snippet))["documents"][0]["source_type"] == (
        "search_snippet"
    )
    context = _initial_context(task)
    by_url = {row["url"]: row for row in context["evidence"]}
    assert by_url["https://acme.com/pricing"]["source_type"] == "vendor"
    assert by_url["https://acme.com/pricing"]["origin"] == "acme.com"
    assert by_url["https://acme.com/pricing"]["published_at"] is None


def test_rules_1_1_host_patterns_are_anchored_to_the_adzuna_label():
    assert load_rules().rules_version == "1.1.0"
    assert classify("web_fetch", "fetched_page", "https://www.adzuna.com/x").rule == (
        "host_pattern:adzuna.*"
    )
    for host in ("adzuna.attacker.example", "notadzuna.com", "adzuna.example.co.uk"):
        assert classify("web_fetch", "fetched_page", f"https://{host}/x").source_type != (
            "job_post"
        )


@pytest.mark.django_db
@override_settings(CACHES=LOCMEM)
def test_canonical_url_types_and_counts_a_proxied_capture_as_the_original_page():
    task = research_task()
    snapshot = "https://web.archive.org/web/20200101000000id_/https://www.trustpilot.com/review/x"
    page = ResearchPage(
        documents=[
            ResearchDocument(
                connector="web_fetch",
                external_id=snapshot,
                url=snapshot,
                text="Archived reviews",
                kind="fetched_page",
                metadata={"canonical_url": "https://www.trustpilot.com/review/x"},
            ),
            ResearchDocument(
                connector="web_fetch",
                external_id="plain",
                url="https://www.trustpilot.com/review/y",
                text="Live reviews",
                kind="fetched_page",
            ),
        ]
    )
    with patch("discovery.connectors.web.fetch_public_url", return_value=page):
        perform_research(
            task, "fetch", {"connector": "web_fetch", "action": "fetch", "target": snapshot}
        )
    archived = task.discovery.evidence.get(url=snapshot)
    assert archived.source_type == "review"
    assert archived.metadata["source_type_rule"] == "domain:trustpilot.com"
    assert archived.origin == "www.trustpilot.com"
    live = task.discovery.evidence.get(url="https://www.trustpilot.com/review/y")
    assert live.source_type == "review" and live.origin == archived.origin
