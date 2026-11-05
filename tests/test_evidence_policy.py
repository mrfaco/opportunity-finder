"""Config 1.3 evidence policy: typed demand/offer rules at Validator acceptance."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from discovery.admin import DiscoveryStartForm
from discovery.agent_tools import AgentTools
from discovery.orchestrator import resolve_config
from discovery.reporting import render_report
from discovery.schemas import DiscoveryConfig, Validation, evidence_settings
from discovery.termination import SubmissionError, evidence_gaps, require_agreement
from tests import discovery_fixtures as fx


def test_config_defaults_and_pre_policy_snapshots():
    config = DiscoveryConfig()
    assert config.schema_version == "1.3"
    assert (config.evidence_policy, config.min_validator_query_novelty) == ("origins", 0.0)
    for version in ("1.0", "1.1", "1.2"):
        old = DiscoveryConfig.model_validate(
            {"schema_version": version, "finder_model": "x/y:batch", "validator_model": "x/y:batch"}
            if version == "1.0"
            else {"schema_version": version}
        )
        assert (old.evidence_policy, old.min_validator_query_novelty) == ("origins", 0.0)
        with pytest.raises(ValidationError, match="require config schema 1.3"):
            DiscoveryConfig.model_validate({"schema_version": version, "evidence_policy": "typed"})
        with pytest.raises(ValidationError, match="require config schema 1.3"):
            DiscoveryConfig.model_validate(
                {"schema_version": version, "min_validator_query_novelty": 0.4}
            )
    # A dumped 1.2 config carries the defaults and re-validates.
    dumped = DiscoveryConfig.model_validate({"schema_version": "1.2"}).model_dump(mode="json")
    assert DiscoveryConfig.model_validate(dumped).schema_version == "1.2"
    typed = resolve_config({"evidence_policy": "typed", "min_validator_query_novelty": 0.4})
    assert typed.model_dump(mode="json")["evidence_policy"] == "typed"
    with pytest.raises(ValidationError):
        DiscoveryConfig(min_validator_query_novelty=1.5)
    with pytest.raises(ValidationError):
        DiscoveryConfig.model_validate({"evidence_policy": "strict"})


def _typed_run(policy="typed"):
    row = fx.discovery(
        config_snapshot={**fx.CONFIG, "schema_version": "1.3", "evidence_policy": policy}
    )
    finder = fx.turn(row, 1, "finder")
    sources = {
        "vendor": fx.evidence(
            finder, "acme.com", url="https://acme.com/pricing", source_type="vendor"
        ),
        "snippet": fx.evidence(
            finder,
            "b.example",
            kind="search_snippet",
            connector="duckduckgo",
            source_type="search_snippet",
        ),
        "news": fx.evidence(finder, "reuters.com", source_type="news"),
        "forum": fx.evidence(finder, "reddit.com", source_type="forum"),
    }
    return row, finder, sources


def _proposal(finder, sources, *, demand, competitors, kind="services"):
    content = {
        "demand_evidence_ids": [str(sources[name].pk) for name in demand],
        "competitor_evidence_ids": [str(sources[name].pk) for name in competitors],
    }
    if kind is not None:
        content["business_model_kind"] = kind
    return fx.proposal(finder, content)


def _accept(row, proposal):
    validator = fx.turn(row, proposal.turn.number + 1, "validator")
    fx.search(validator, fx.DISTINCT_QUERY, found=1)
    validation = Validation(
        reviewed_proposal_id=str(proposal.pk),
        reviewed_proposal_hash=proposal.content_hash,
        verdict="accept",
        summary="Accept",
        falsification_checks=["Looked for contrary evidence"],
    )
    return validator.task, validation


@pytest.mark.django_db
def test_typed_refuses_vendor_and_snippet_demand_with_hint_then_passes_with_forum():
    row, finder, sources = _typed_run()
    proposal = _proposal(
        finder, sources, demand=["vendor", "snippet"], competitors=["vendor", "news"]
    )
    task, validation = _accept(row, proposal)
    with pytest.raises(SubmissionError) as refused:
        require_agreement(row, task, proposal, validation)
    message = str(refused.value)
    assert message.startswith("Demand evidence needs one independent source")
    assert f"{sources['vendor'].pk}: vendor" in message
    assert f"{sources['snippet'].pk}: search_snippet" in message
    assert refused.value.finder_must_revise

    with AgentTools(task, []) as bridge, patch.object(bridge, "call", side_effect=refused.value):
        response = bridge.handle({"method": "tools/call", "params": {"name": "submit_validation"}})
    result = json.loads(response["content"][0]["text"])
    assert result["status"] == "validation_failed"
    assert "Submit a challenge" in result["recovery_hint"]
    assert "forum" in result["recovery_hint"] and "statistics" in result["recovery_hint"]
    assert "Enabled connectors most likely to supply it: web_fetch." in result["recovery_hint"]

    revised = _proposal(
        fx.turn(row, 3, "finder"),
        sources,
        demand=["vendor", "forum"],
        competitors=["vendor", "news"],
    )
    task, validation = _accept(row, revised)
    require_agreement(row, task, revised, validation)


@pytest.mark.django_db
def test_offer_level_competition_applies_to_physical_and_service_kinds_only():
    row, finder, sources = _typed_run()
    services = _proposal(finder, sources, demand=["forum"], competitors=["news"])
    gaps = evidence_gaps(row, services)
    assert len(gaps) == 1
    assert gaps[0].startswith("Competitor evidence for a services business needs one offer-level")
    assert f"{sources['news'].pk}: news" in gaps[0]
    software = _proposal(
        fx.turn(row, 2, "finder"), sources, demand=["forum"], competitors=["news"], kind="software"
    )
    assert evidence_gaps(row, software) == []
    # Contract-1.0 content (no kind) is judged by the independent-demand rule only.
    legacy = _proposal(
        fx.turn(row, 3, "finder"), sources, demand=["vendor"], competitors=["news"], kind=None
    )
    assert [gap.split(";")[0] for gap in evidence_gaps(row, legacy)] == [
        "Demand evidence needs one independent source (forum, review, job post, tender, "
        "regulator, statistics, news or marketplace listing)"
    ]


@pytest.mark.django_db
def test_origins_policy_is_unchanged_and_unflagged_errors_get_no_hint():
    row, finder, sources = _typed_run(policy="origins")
    proposal = _proposal(finder, sources, demand=["vendor", "snippet"], competitors=["news"])
    assert evidence_gaps(row, proposal) == []
    one_origin = _proposal(
        fx.turn(row, 2, "finder"), sources, demand=["vendor"], competitors=["vendor"]
    )
    task, validation = _accept(row, one_origin)
    with pytest.raises(
        SubmissionError, match="^Two distinct source origins are required$"
    ) as refused:
        require_agreement(row, task, one_origin, validation)
    assert refused.value.finder_must_revise and not refused.value.missing_source_types
    with AgentTools(task, []) as bridge:
        with patch.object(bridge, "call", side_effect=refused.value):
            flagged = bridge.handle(
                {"method": "tools/call", "params": {"name": "submit_validation"}}
            )
        with patch.object(
            bridge, "call", side_effect=SubmissionError("Reviewed proposal hash does not match")
        ):
            plain = bridge.handle({"method": "tools/call", "params": {"name": "submit_validation"}})
    hint = json.loads(flagged["content"][0]["text"])["recovery_hint"]
    assert "Missing source type" not in hint
    assert "recovery_hint" not in json.loads(plain["content"][0]["text"])


def test_start_form_freezes_policy_in_a_one_point_three_config():
    form = DiscoveryStartForm(
        {
            "objective": "Find a service business",
            "max_turns": 10,
            "max_cost_usd": "5",
            "max_duration_s": 600,
            "max_model_calls_per_turn": 8,
            "max_research_calls_per_turn": 8,
            "max_output_tokens": 24000,
            "finder_provider": "openrouter",
            "finder_model": "deepseek/deepseek-v4.1-flash",
            "validator_provider": "openrouter",
            "validator_model": "deepseek/deepseek-v4.1-flash",
            "judge_provider": "ollama",
            "judge_model": "kimi-k3:cloud",
            "connectors": ["search_auto", "web_fetch"],
            "evidence_policy": "typed",
            "min_validator_query_novelty": "0.4",
            "idempotency_key": str(uuid4()),
        }
    )
    assert form.is_valid(), form.errors
    config = DiscoveryConfig.model_validate(form.discovery_config())
    assert config.schema_version == "1.3"
    assert (config.evidence_policy, config.min_validator_query_novelty) == ("typed", 0.4)
    assert "evidence_policy" in DiscoveryStartForm().fields


@pytest.mark.django_db
def test_report_configuration_block_shows_the_frozen_policy():
    old = fx.discovery()
    assert (
        "Evidence policy: origins; minimum Validator query novelty: 0.00."
        in (render_report(old)[0])
    )
    typed = fx.discovery(
        config_snapshot={
            **fx.CONFIG,
            "schema_version": "1.3",
            "evidence_policy": "typed",
            "min_validator_query_novelty": 0.4,
        }
    )
    assert (
        "Evidence policy: typed; minimum Validator query novelty: 0.40."
        in (render_report(typed)[0])
    )


def test_evidence_settings_read_only_the_two_fields():
    # Other settings that today's validation rejects do not block reading a run's policy.
    stale_config = {"schema_version": "1.3", "finder_provider": "anthropic", "finder_model": "a/b"}
    assert evidence_settings({**stale_config, "evidence_policy": "typed"}).evidence_policy == (
        "typed"
    )
    assert evidence_settings({"schema_version": "1.2"}).evidence_policy == "origins"
    assert evidence_settings({}).min_validator_query_novelty == 0.0
    with pytest.raises(ValidationError):
        evidence_settings({"schema_version": "1.3", "min_validator_query_novelty": 2})
