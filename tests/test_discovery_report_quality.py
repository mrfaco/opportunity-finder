"""Every surface that shows a discovery shows its evidence quality and human verdict."""

from __future__ import annotations

import pytest

from api.models import ApiKey
from discovery.models import Discovery
from discovery.orchestrator import regenerate_report
from discovery.schemas import Report
from discovery.verdicts import record_verdict
from tests import discovery_fixtures as fx

pytestmark = pytest.mark.django_db


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_regenerated_report_is_1_2_with_metrics_and_null_verdict():
    row, _ = fx.evidenced_discovery()
    # A pre-migration stored report is not rewritten until regeneration.
    Discovery.objects.filter(pk=row.pk).update(report_json={"schema_version": "1.1"})
    report = regenerate_report(row.pk).report_json
    assert report["schema_version"] == "1.2"
    assert report["human_verdict"] is None
    assert report["quality"]["schema_version"] == "1.2"
    assert report["quality"]["cited_origin_count"] == 3
    assert report["quality"]["validator_query_novelty_min"] == pytest.approx(2 / 11)
    text = Discovery.objects.get(pk=row.pk).report_markdown
    quality = _section(text, "### Evidence quality", "Discovery ID:")
    assert (
        "Cited sources: 4 from 3 distinct origins (bing 1, duckduckgo 1, web_fetch 2)." in quality
    )
    assert "Search snippets among cited sources: 25%." in quality
    assert "median age when known: 20 days" in quality
    assert "lowest 0.18, accepting turn 1.00" in quality
    assert "No human verdict recorded." in quality
    assert text.index("## Limitations and provenance") < text.index("### Evidence quality")
    assert "Human verdict:" not in text


def test_recorded_verdict_appears_in_final_assessment_and_quality_section():
    row, _ = fx.evidenced_discovery()
    record_verdict(row, "weak", "Demand is thin", user=None, via="api")
    record_verdict(row, "good", "", user=None, via="api")
    row.refresh_from_db()
    text = row.report_markdown
    assessment = _section(text, "## Final assessment", "## Judge assessment")
    assert "**Human verdict: good** (" in assessment and "via api)" in assessment
    assert "Demand is thin" not in assessment
    assert "- **Human verdict: good**" in _section(text, "### Evidence quality", "Discovery ID:")
    assert row.report_json["human_verdict"]["verdict"] == "good"
    assert row.report_json["quality"]["human_verdict"] == "good"


def test_unknown_metrics_are_reported_as_unknown_with_reasons():
    row = Discovery.objects.create(objective="Failed early", request_hash="x", status="failed")
    report = regenerate_report(row.pk)
    quality = _section(report.report_markdown, "### Evidence quality", "Discovery ID:")
    assert "Cited sources: unknown from unknown distinct origins." in quality
    assert "Unknown measures:" in quality and "No Finder proposal" in quality


def test_stored_1_1_report_json_still_deserializes():
    row, _ = fx.evidenced_discovery()
    current = regenerate_report(row.pk).report_json
    legacy = {
        key: value for key, value in current.items() if key not in {"quality", "human_verdict"}
    }
    legacy["schema_version"] = "1.1"
    restored = Report.model_validate(legacy)
    assert restored.schema_version == "1.1" and restored.quality is None
    assert Report.model_validate(current).quality is not None


def test_api_detail_carries_quality_only_when_terminal(client, django_user_model):
    user = django_user_model.objects.create_user(username="apiuser", password="pass")
    _, raw = ApiKey.create_for_user(user=user, label="test")
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    row, _ = fx.evidenced_discovery()
    detail = client.get(f"/api/v1/discoveries/{row.pk}/", **headers).json()
    assert detail["schema_version"] == "1.2"
    assert detail["quality"]["cited_evidence_count"] == 4
    Discovery.objects.filter(pk=row.pk).update(status="running")
    detail = client.get(f"/api/v1/discoveries/{row.pk}/", **headers).json()
    assert detail["quality"] is None


def test_admin_review_shows_metrics_table_for_terminal_discoveries(client, admin_user):
    row, _ = fx.evidenced_discovery()
    client.force_login(admin_user)
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    table = _section(page, 'id="evidence-quality"', "</div>")
    assert "<td>cited_origin_count</td><td>3</td>" in table
    assert "<td>median_evidence_age_days</td><td>20</td>" in table
    regenerate_report(row.pk)
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    assert page.index('id="evidence-quality"') < page.index(">Final report<")
    Discovery.objects.filter(pk=row.pk).update(status="running")
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    assert 'id="evidence-quality"' not in page


def test_report_prints_source_types_shares_and_staleness():
    row, _ = fx.typed_discovery()
    text = regenerate_report(row.pk).report_markdown
    quality = _section(text, "### Evidence quality", "Discovery ID:")
    assert "older than 24 months at retrieval: 50% of dated sources." in quality
    assert "Cited source types: forum 1, news 1, vendor 1." in quality
    assert (
        "Demand citations from an independent source type: 50%; competitor citations at "
        "offer level: 50% (evidence policy typed)." in quality
    )


def test_report_prints_transaction_signal_and_specialist_share():
    row, _ = fx.typed_discovery()
    quality = _section(
        regenerate_report(row.pk).report_markdown, "### Evidence quality", "Discovery ID:"
    )
    assert (
        "Transaction signal cited (job post, tender or marketplace listing): no; cited "
        "sources from specialist connectors: 0%." in quality
    )
    failed = Discovery.objects.create(objective="Failed early", request_hash="y", status="failed")
    quality = _section(
        regenerate_report(failed.pk).report_markdown, "### Evidence quality", "Discovery ID:"
    )
    assert "listing): unknown; cited sources from specialist connectors: unknown." in quality
