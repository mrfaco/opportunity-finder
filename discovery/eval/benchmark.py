"""Versioned, git-managed benchmark objectives for discovery quality comparisons."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from discovery.schemas import Contract

BENCHMARK_PATH = Path(__file__).resolve().parent / "objectives.json"
BusinessModelFocus = Literal["services", "manufacturing", "intelligence", "goods", "software"]


class BenchmarkCompositionError(ValueError):
    """The benchmark set breaks a composition rule; ``rule`` names it."""

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"{rule}: {detail}")
        self.rule = rule


class Rubric(Contract):
    must_cite: list[str] = Field(min_length=1)
    red_flags: list[str] = Field(min_length=1)


class BenchmarkObjective(Contract):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=64)
    objective: str = Field(min_length=1, max_length=12000)
    constraints: str = Field(default="", max_length=12000)
    business_model_focus: list[BusinessModelFocus] = Field(min_length=1)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    language: str = Field(pattern=r"^[a-z]{2}$")
    rubric: Rubric


class BenchmarkSet(Contract):
    objectives: list[BenchmarkObjective] = Field(min_length=1)


def check_composition(benchmark: BenchmarkSet) -> None:
    objectives = benchmark.objectives
    ids = [item.id for item in objectives]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise BenchmarkCompositionError("unique_ids", f"duplicate objective IDs {duplicates}")
    non_software = [item for item in objectives if "software" not in item.business_model_focus]
    if len(non_software) < 4:
        raise BenchmarkCompositionError(
            "non_software_minimum",
            f"{len(non_software)} objectives exclude software; at least 4 are required",
        )
    non_english = [item for item in objectives if item.language != "en"]
    if len(non_english) < 4:
        raise BenchmarkCompositionError(
            "non_english_minimum",
            f"{len(non_english)} objectives are not in English; at least 4 are required",
        )
    regions = {item.region for item in objectives}
    if len(regions) < 3:
        raise BenchmarkCompositionError(
            "region_minimum", f"{len(regions)} regions; at least 3 are required"
        )
    seen: dict[tuple[str, frozenset[str]], str] = {}
    for item in objectives:
        key = (item.region, frozenset(item.business_model_focus))
        if key in seen:
            raise BenchmarkCompositionError(
                "unique_region_focus",
                f"{seen[key]} and {item.id} share region {item.region} and focus {sorted(key[1])}",
            )
        seen[key] = item.id


def load_benchmark(path: Path = BENCHMARK_PATH) -> BenchmarkSet:
    benchmark = BenchmarkSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    check_composition(benchmark)
    return benchmark
