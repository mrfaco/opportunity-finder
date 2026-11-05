"""Benchmark runs are dry by default, bounded by an authorized total and idempotent."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from discovery.models import Discovery

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    out = StringIO()
    with (
        patch("discovery.runtime.check_configuration", return_value={"profiles": {}}),
        patch("discovery.orchestrator._kick_dispatch") as dispatch,
    ):
        call_command("run_discovery_benchmark", *args, stdout=out)
    dispatch.assert_not_called()  # on_commit never fires inside the test transaction
    return out.getvalue()


def test_dry_run_prints_twelve_keys_and_starts_nothing():
    output = _run("--tag", "t1")
    assert output.count("benchmark:t1:") == 12
    assert output.count("  planned") == 12
    assert "Ceiling: 12 × USD 10.00 = USD 120.00" in output
    assert "finder:" in output and "judge:" in output
    assert '"max_cost_usd": "10.00"' in output
    assert "Dry run: nothing started" in output
    assert not Discovery.objects.exists()


def test_authorized_run_respects_ceiling_and_is_idempotent():
    with pytest.raises(CommandError, match="ceiling sum USD 120.00 exceeds"):
        _run("--tag", "t1", "--authorize-spend", "--max-total-usd", "50")
    assert not Discovery.objects.exists()
    with pytest.raises(CommandError, match="requires --max-total-usd"):
        _run("--tag", "t1", "--authorize-spend")
    output = _run("--tag", "t1", "--authorize-spend", "--max-total-usd", "120")
    assert output.count("  started ") == 12
    rows = Discovery.objects.filter(benchmark_tag="t1")
    assert rows.count() == 12
    row = rows.get(benchmark_objective_id="ar-cyber-smb")
    assert row.idempotency_key == "benchmark:t1:ar-cyber-smb"
    assert row.objective.startswith("Look for opportunities in cybersecurity")
    assert row.constraints.startswith("Argentina")
    assert row.tasks.count() == 1
    again = _run("--tag", "t1", "--authorize-spend", "--max-total-usd", "120")
    assert again.count("  exists ") == 12 and "started" not in again
    assert str(row.pk) in again
    assert Discovery.objects.count() == 12
    dry = _run("--tag", "t1")
    assert dry.count("  exists ") == 12


def test_subset_starts_only_selected_objectives():
    output = _run(
        "--tag",
        "subset",
        "--objective",
        "ar-cyber-smb",
        "--objective",
        "fr-bakery-equipment",
        "--authorize-spend",
        "--max-total-usd",
        "20",
    )
    assert output.count("  started ") == 2
    assert set(Discovery.objects.values_list("benchmark_objective_id", flat=True)) == {
        "ar-cyber-smb",
        "fr-bakery-equipment",
    }
    with pytest.raises(CommandError, match="Unknown benchmark objective"):
        _run("--tag", "subset", "--objective", "nope")


def test_config_override_is_frozen_and_validated_before_any_start():
    output = _run(
        "--tag",
        "cheap",
        "--config-json",
        '{"max_cost_usd": "2.5", "max_turns": 20}',
        "--authorize-spend",
        "--max-total-usd",
        "30",
    )
    assert "USD 30.0" in output
    assert {row.config_snapshot["max_cost_usd"] for row in Discovery.objects.all()} == {"2.5"}
    Discovery.objects.all().delete()
    for bad in ('{"max_turns": 1}', "[1]", "{nope", '{"unknown_field": 1}'):
        with pytest.raises(CommandError):
            _run(
                "--tag", "bad", "--config-json", bad, "--authorize-spend", "--max-total-usd", "999"
            )
    assert not Discovery.objects.exists()


def test_invalid_tag_and_amount_are_rejected():
    with pytest.raises(CommandError, match="--tag"):
        _run("--tag", "Bad_Tag")
    with pytest.raises(CommandError):
        _run("--tag", "t1", "--max-total-usd", "lots")


def test_review_page_shows_rubric_for_benchmark_discoveries(client, admin_user):
    _run("--tag", "t1", "--objective", "ar-cyber-smb", "--authorize-spend", "--max-total-usd", "10")
    row = Discovery.objects.get()
    client.force_login(admin_user)
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    assert "Benchmark t1 · ar-cyber-smb" in page
    assert "a competitor price or offer" in page
    assert "overseas BPO staffed in Argentina" in page
    Discovery.objects.filter(pk=row.pk).update(benchmark_objective_id="retired-id")
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    assert "no longer in the benchmark set" in page
    Discovery.objects.filter(pk=row.pk).update(benchmark_objective_id="", benchmark_tag="")
    page = client.get(f"/admin/discovery/discovery/{row.pk}/review/").content.decode()
    assert "benchmark-rubric" not in page
