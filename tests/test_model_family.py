"""Model families: the rule, the quality metric, the report flag and the start-form warning."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.messages import get_messages
from django.test import override_settings
from django.urls import reverse

from discovery.models import Discovery
from discovery.quality import compute_quality
from discovery.reporting import render_report
from discovery.schemas import model_family, same_model_family
from tests import discovery_fixtures as fx

DEEPSEEK_VARIANTS = {
    "finder_provider": "openrouter",
    "finder_model": "deepseek/deepseek-v4.1-flash",
    "validator_provider": "ollama",
    "validator_model": "deepseek-v4.1-flash:cloud",
}
# A stand-in for a different family; the deployment default is chosen separately.
DIFFERENT_FAMILIES = {**DEEPSEEK_VARIANTS, "validator_provider": "openrouter"} | {
    "validator_model": "qwen/qwen3-max"
}


@pytest.mark.parametrize(
    "provider,model,family",
    [
        ("openrouter", "deepseek/deepseek-v4.1-flash", "deepseek"),
        ("openrouter", "deepseek/deepseek-r2", "deepseek"),
        ("openrouter", "Qwen/qwen3-max", "qwen"),
        ("openrouter", "anthropic/claude-sonnet-5", "anthropic"),
        ("anthropic", "sonnet", "anthropic"),
        ("ollama", "kimi-k3:cloud", "kimi"),
        ("ollama", "qwen3:32b", "qwen"),
        ("ollama", "llama3.1", "llama"),
        ("ollama", "glm-5.3-flash:cloud", "glm"),
    ],
)
def test_family_rule_per_provider(provider, model, family):
    assert model_family(provider, model) == family


@pytest.mark.parametrize(
    "provider,model",
    [("openrouter", "deepseek-v4.1-flash"), ("ollama", "3b-model"), ("bedrock", "x")],
)
def test_unclassifiable_models_fail_loudly(provider, model):
    with pytest.raises(ValueError):
        model_family(provider, model)


def test_same_family_compares_families_not_strings():
    assert same_model_family(DEEPSEEK_VARIANTS)
    assert not same_model_family(DIFFERENT_FAMILIES)
    # Configs from before per-role providers ran OpenRouter only.
    assert same_model_family(
        {"finder_model": "deepseek/deepseek-v4.1-flash:batch", "validator_model": "deepseek/x"}
    )
    assert not same_model_family({"finder_model": "deepseek/deepseek-v4.1-flash"})


@pytest.mark.django_db
def test_metric_and_report_flag_two_variants_of_one_family():
    row = fx.discovery(config_snapshot={**fx.CONFIG, **DEEPSEEK_VARIANTS})
    assert compute_quality(row).same_model_family is True
    _, report = render_report(row)
    flags = [value for value in report["limitations"] if "same family" in value]
    assert flags == [
        "Finder (deepseek/deepseek-v4.1-flash) and Validator (deepseek-v4.1-flash:cloud) used "
        "models from the same family, deepseek. Separate sessions do not remove shared model "
        "blind spots; consider a Validator from a different model family."
    ]


@pytest.mark.django_db
def test_metric_and_report_do_not_flag_different_families():
    row = fx.discovery(config_snapshot={**fx.CONFIG, **DIFFERENT_FAMILIES})
    assert compute_quality(row).same_model_family is False
    _, report = render_report(row)
    assert not any("blind spots" in value for value in report["limitations"])


@pytest.mark.django_db
def test_start_form_warns_on_one_family_by_default(admin_client):
    with override_settings(
        DISCOVERY_FINDER_MODEL="deepseek/deepseek-v4.1-flash",
        DISCOVERY_VALIDATOR_MODEL="deepseek/deepseek-v4.1-flash",
    ):
        page = admin_client.get(reverse("admin:discovery-start"))
    assert "both use the deepseek model family" in page.content.decode()
    with override_settings(DISCOVERY_VALIDATOR_MODEL="qwen/qwen3-max"):
        page = admin_client.get(reverse("admin:discovery-start"))
    assert "both use the" not in page.content.decode()


@pytest.mark.django_db
def test_start_form_warning_does_not_block_the_discovery(admin_client):
    data = {
        "objective": "Find a services business",
        "max_turns": 100,
        "max_cost_usd": "10.00",
        "max_duration_s": 1800,
        "max_model_calls_per_turn": 8,
        "max_research_calls_per_turn": 8,
        "max_output_tokens": 24000,
        "finder_provider": "openrouter",
        "finder_model": "deepseek/deepseek-v4.1-flash",
        "validator_provider": "ollama",
        "validator_model": "deepseek-v4.1-flash:cloud",
        "judge_enabled": "on",
        "judge_provider": "ollama",
        "judge_model": "kimi-k3:cloud",
        "connectors": ["search_auto", "web_fetch"],
        "evidence_policy": "origins",
        "min_validator_query_novelty": 0.0,
        "idempotency_key": str(uuid4()),
    }
    with (
        patch("discovery.runtime.check_configuration", return_value={"profiles": {}}),
        patch("discovery.orchestrator._kick_dispatch"),
    ):
        response = admin_client.post(reverse("admin:discovery-start"), data)
    assert response.status_code == 302
    assert Discovery.objects.count() == 1
    notices = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("both use the deepseek model family" in notice for notice in notices)
