"""The git-managed benchmark set cannot drift from its composition rules."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from api.models import ApiKey
from discovery.eval.benchmark import (
    BENCHMARK_PATH,
    BenchmarkCompositionError,
    BenchmarkSet,
    check_composition,
    load_benchmark,
)
from tests import discovery_fixtures as fx


def _raw() -> dict:
    return json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))


def _rule(raw: dict) -> str:
    with pytest.raises(BenchmarkCompositionError) as caught:
        check_composition(BenchmarkSet.model_validate(raw))
    return caught.value.rule


def test_committed_set_loads_and_passes_every_rule():
    benchmark = load_benchmark()
    assert benchmark.schema_version == "1.0"
    assert len(benchmark.objectives) == 12
    languages = {item.language for item in benchmark.objectives} - {"en"}
    assert len(languages) >= 4
    focus = {value for item in benchmark.objectives for value in item.business_model_focus}
    assert {"services", "manufacturing", "intelligence", "goods", "software"} <= focus
    assert all(item.rubric.must_cite and item.rubric.red_flags for item in benchmark.objectives)


def test_duplicate_id_is_rejected():
    raw = _raw()
    raw["objectives"][1]["id"] = raw["objectives"][0]["id"]
    assert _rule(raw) == "unique_ids"


def test_too_few_non_software_objectives_is_rejected():
    raw = _raw()
    for item in raw["objectives"][3:]:
        item["business_model_focus"] = [*item["business_model_focus"], "software"]
    assert _rule(raw) == "non_software_minimum"


def test_too_few_non_english_objectives_is_rejected():
    raw = _raw()
    for item in raw["objectives"][3:]:
        item["language"] = "en"
    assert _rule(raw) == "non_english_minimum"


def test_too_few_regions_is_rejected():
    raw = _raw()
    for index, item in enumerate(raw["objectives"]):
        item["region"] = "AR" if index % 2 else "MX"
        item["business_model_focus"] = [["services", "goods", "software"][index % 3]]
        item["business_model_focus"].append(["intelligence", "manufacturing"][index // 6])
    assert _rule(raw) == "region_minimum"


def test_identical_region_focus_pair_is_rejected_regardless_of_order():
    raw = _raw()
    first = raw["objectives"][0]
    raw["objectives"][1]["region"] = first["region"]
    raw["objectives"][1]["business_model_focus"] = list(reversed(first["business_model_focus"]))
    assert _rule(raw) == "unique_region_focus"


def test_load_runs_contract_and_composition(tmp_path):
    path = tmp_path / "objectives.json"
    raw = _raw()
    raw["objectives"][0]["unexpected"] = True
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_benchmark(path)
    raw = _raw()
    raw["objectives"].append(dict(raw["objectives"][0]))
    path.write_text(json.dumps(raw))
    with pytest.raises(BenchmarkCompositionError, match="unique_ids"):
        load_benchmark(path)


@pytest.mark.django_db
def test_benchmark_fields_are_exposed_read_only(client, admin_user, django_user_model):
    row = fx.discovery(benchmark_tag="t1", benchmark_objective_id="ar-cyber-smb")
    fx.discovery(request_hash="other")
    user = django_user_model.objects.create_user(username="apiuser", password="pass")
    _, raw = ApiKey.create_for_user(user=user, label="test")
    detail = client.get(f"/api/v1/discoveries/{row.pk}/", HTTP_AUTHORIZATION=f"Bearer {raw}").json()
    assert detail["benchmark_tag"] == "t1"
    assert detail["benchmark_objective_id"] == "ar-cyber-smb"
    client.force_login(admin_user)
    page = client.get("/admin/discovery/discovery/?benchmark_tag=t1")
    assert page.status_code == 200
    assert page.context["cl"].result_count == 1
