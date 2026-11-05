"""Proposal contract 1.1: a structured business-model kind, with 1.0 content read as unspecified."""

import pytest
from pydantic import ValidationError

from discovery.loop import tools_for_role
from discovery.reporting import render_report
from discovery.schemas import Proposal
from discovery.termination import proposal_kind
from tests.discovery_fixtures import discovery, proposal, turn

BRIEF = {
    "title": "Repair service",
    "target_customer": "Travellers",
    "buyer": "Traveller",
    "problem": "Damaged luggage",
    "offering": "Same-day repair",
    "business_model": "Fee per repair",
    "customer_relationship": "B2C",
    "revenue_hypothesis": "Per-job fee",
    "delivery_model": "Mobile technician",
    "distribution_hypothesis": "Hotel referrals",
    "summary": "Repairs at the airport.",
}


def test_contract_requires_a_known_kind():
    with pytest.raises(ValidationError, match="business_model_kind"):
        Proposal.model_validate(BRIEF)
    with pytest.raises(ValidationError, match="business_model_kind"):
        Proposal.model_validate({**BRIEF, "business_model_kind": "saas"})
    with pytest.raises(ValidationError, match="schema_version"):
        Proposal.model_validate(
            {**BRIEF, "business_model_kind": "services", "schema_version": "1.0"}
        )
    parsed = Proposal.model_validate({**BRIEF, "business_model_kind": "services"})
    assert parsed.schema_version == "1.1"
    assert parsed.business_model == "Fee per repair"


def test_finder_tool_schema_advertises_the_kind():
    submit = next(
        tool["function"]
        for tool in tools_for_role("finder")
        if tool["function"]["name"] == "submit_proposal"
    )
    schema = submit["parameters"]
    assert "business_model_kind" in schema["required"]
    kind = schema["properties"]["business_model_kind"]
    values = kind.get("enum") or schema["$defs"][kind["$ref"].split("/")[-1]]["enum"]
    assert values == [
        "services",
        "manufacturing",
        "goods",
        "intelligence",
        "software",
        "marketplace",
        "other",
    ]


def test_proposal_kind_reads_stored_one_point_zero_content_as_unspecified():
    assert proposal_kind(BRIEF) == "unspecified"
    assert proposal_kind({**BRIEF, "business_model_kind": "goods"}) == "goods"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("content", "expected"),
    [(BRIEF, "unspecified"), ({**BRIEF, "business_model_kind": "services"}, "services")],
)
def test_report_renders_the_kind(content, expected):
    row = discovery()
    proposal(turn(row, 1, "finder"), content)
    markdown, report = render_report(row)
    assert f"**Business model kind:** {expected}" in markdown
    assert report["final_proposal"].get("business_model_kind") == content.get("business_model_kind")
