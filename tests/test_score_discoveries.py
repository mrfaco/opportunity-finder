"""Scoring passes persist comparable aggregates without recomputation."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from discovery.models import DiscoveryQualityRun
from discovery.quality import aggregate_metrics, compute_quality
from discovery.schemas import QualityMetrics
from tests import discovery_fixtures as fx

pytestmark = pytest.mark.django_db


def _score(*args: str) -> tuple[str, str]:
    out, err = StringIO(), StringIO()
    call_command("score_discoveries", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _three(tag: str = "t1", prompts: dict | None = None):
    evidenced, _ = fx.evidenced_discovery(benchmark_tag=tag, benchmark_objective_id="a")
    bare = fx.discovery(
        benchmark_tag=tag,
        benchmark_objective_id="b",
        status="failed",
        outcome="inconclusive",
        prompt_snapshot=prompts or fx.PROMPTS,
    )
    running = fx.discovery(benchmark_tag=tag, benchmark_objective_id="c", status="running")
    return evidenced, bare, running


def test_scores_terminal_discoveries_and_names_skipped_ones():
    evidenced, bare, running = _three()
    output, _ = _score("--tag", "t1")
    run = DiscoveryQualityRun.objects.get()
    assert run.tag == "t1" and run.schema_version == "1.0"
    assert run.metrics_schema_version == "1.0"
    assert set(run.discovery_ids) == {str(evidenced.pk), str(bare.pk)}
    assert len(run.per_discovery) == 2
    assert run.config_snapshots_distinct == 1
    assert run.prompt_hashes == {
        "finder": ["finder-hash"],
        "judge": ["judge-hash"],
        "validator": ["validator-hash"],
    }
    cited = run.aggregate["cited_evidence_count"]
    # The bare discovery has no proposal: excluded and counted, never averaged as zero.
    assert cited == {
        "count": 1,
        "none_count": 1,
        "mean": 4.0,
        "median": 4.0,
        "min": 4.0,
        "max": 4.0,
        "sum": 4.0,
    }
    assert run.aggregate["turns_completed"]["count"] == 2
    assert run.aggregate["outcome"]["distribution"] == {"converged": 1, "inconclusive": 1}
    assert run.aggregate["human_verdict"]["none_count"] == 2
    assert run.aggregate["known_cost_usd"]["sum"] == 1.0
    assert f"Skipped {running.pk}: running, not terminal." in output
    assert "| cited_evidence_count | 4 | 4 | 4 | 4 | 4 | 1 | 1 |" in output
    assert "Compared with" not in output


def test_compare_flags_prompt_changes_and_prints_deltas():
    _three()
    _score("--tag", "t1")
    first = DiscoveryQualityRun.objects.get()
    output, _ = _score("--tag", "t1", "--compare-to", str(first.pk))
    assert f"Compared with `{first.pk}`" in output
    assert "WARNING" not in output
    assert "| turns_completed | 2.5 | 2.5 | 0 |" in output
    changed = {**fx.PROMPTS, "finder": {**fx.PROMPTS["finder"], "hash": "finder-v2"}}
    _three(tag="t2", prompts=changed)
    output, _ = _score("--tag", "t2", "--compare-to", str(first.pk))
    assert "WARNING: prompt hashes differ" in output
    assert "WARNING: discovery configurations differ" not in output
    assert DiscoveryQualityRun.objects.count() == 3


def test_compare_flags_metrics_schema_and_config_differences():
    _three()
    _score("--tag", "t1")
    first = DiscoveryQualityRun.objects.get()
    DiscoveryQualityRun.objects.filter(pk=first.pk).update(metrics_schema_version="0.9")
    fx.discovery(benchmark_tag="t3", config_snapshot={**fx.CONFIG, "max_turns": 20})
    output, _ = _score("--tag", "t3", "--compare-to", str(first.pk))
    assert "WARNING: metrics schema changed (0.9 → 1.0)" in output
    assert "WARNING: discovery configurations differ" in output


def test_json_output_round_trips_through_the_contract():
    evidenced, bare, running = _three()
    output, err = _score(
        "--discovery", str(evidenced.pk), "--discovery", str(running.pk), "--format", "json"
    )
    body = json.loads(output)
    assert f"Skipped {running.pk}" in err
    assert body["discovery_ids"] == [str(evidenced.pk)] and body["tag"] == ""
    restored = [QualityMetrics.model_validate(row) for row in body["per_discovery"]]
    assert restored == [compute_quality(evidenced)]
    assert body["aggregate"] == json.loads(json.dumps(aggregate_metrics(restored)))


def test_selection_errors_write_nothing():
    _, _, running = _three()
    with pytest.raises(CommandError, match="No terminal discoveries"):
        _score("--discovery", str(running.pk))
    with pytest.raises(CommandError, match="Unknown discovery IDs"):
        _score("--discovery", "00000000-0000-0000-0000-000000000000")
    with pytest.raises(CommandError, match="Invalid discovery ID"):
        _score("--discovery", "not-a-uuid")
    with pytest.raises(CommandError, match="Unknown quality run"):
        _score("--tag", "t1", "--compare-to", "00000000-0000-0000-0000-000000000000")
    with pytest.raises(CommandError, match="No terminal discoveries"):
        _score("--tag", "t1", "--since", "2999-01-01")
    assert not DiscoveryQualityRun.objects.exists()
