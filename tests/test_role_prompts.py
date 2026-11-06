"""Effective role prompts: assembly, hashing, snapshot 1.1 and the word budget."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.management import call_command

from agents.prompts import canonicalize, load_prompt
from discovery import loop
from discovery.claude_runtime import build_launch
from discovery.orchestrator import claim_task, start_discovery
from discovery.prompts import role_prompt
from discovery.quality import compute_quality
from discovery.schemas import DiscoveryConfig
from tests import discovery_fixtures as fx

ROLES = ("finder", "validator", "judge")
# Effective prompts are read on every turn of every role. Raising this is a
# deliberate change with its reason in the commit body.
WORD_BUDGET = 2400


def _within_budget(content: str) -> None:
    words = len(content.split())
    assert words <= WORD_BUDGET, f"effective prompt has {words} words; budget is {WORD_BUDGET}"


def _write(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        f'---\nschema_version: "1.0"\ndescription: Synthetic\n---\n{body}', encoding="utf-8"
    )


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.prompts.PROMPTS_DIR", tmp_path)
    return tmp_path


def test_procedure_is_appended_and_the_hash_tracks_both_files(prompts_dir):
    _write(prompts_dir / "finder", "system.md", "You are Finder.\n")
    _write(prompts_dir / "finder", "procedural.md", "1. Decompose the objective.\n")
    first = role_prompt("finder")
    assert first.content == "You are Finder.\n\n1. Decompose the objective.\n"
    assert first.hash == hashlib.sha256(canonicalize(first.content).encode()).hexdigest()
    assert [(part.kind, part.path) for part in first.parts] == [
        ("system", "prompts/finder/system.md"),
        ("procedural", "prompts/finder/procedural.md"),
    ]
    assert [part.schema_version for part in first.parts] == ["1.0", "1.0"]
    assert first.parts[1].hash == load_prompt("finder", "procedural").hash
    _write(prompts_dir / "finder", "procedural.md", "1. Decompose the objective first.\n")
    second = role_prompt("finder")
    assert second.hash != first.hash
    _write(prompts_dir / "finder", "system.md", "You are Finder, a researcher.\n")
    assert role_prompt("finder").hash not in {first.hash, second.hash}


def test_role_without_a_procedure_has_one_part_and_the_system_hash(prompts_dir):
    _write(prompts_dir / "judge", "system.md", "You are Judge.\n")
    prompt = role_prompt("judge")
    system = load_prompt("judge", "system")
    assert prompt.content == system.content
    assert prompt.hash == system.hash
    assert [part.kind for part in prompt.parts] == ["system"]


@pytest.mark.django_db
def test_new_discovery_freezes_schema_1_1_snapshot_with_parts():
    with patch("discovery.runtime.check_configuration", return_value={"profiles": {}}):
        row = start_discovery("Find a services business", enqueue=False)
    assert set(row.prompt_snapshot) == set(ROLES)
    for role in ROLES:
        entry = row.prompt_snapshot[role]
        current = role_prompt(role)
        assert entry["schema_version"] == "1.1"
        assert entry["content"] == current.content
        assert entry["hash"] == current.hash
        assert entry["path"] == f"prompts/{role}/system.md"
        assert entry["parts"] == [part.model_dump() for part in current.parts]


class _Halt(Exception):
    """Stops the batch loop at the first provider call, after the turn inputs are saved."""


@pytest.mark.django_db
@pytest.mark.parametrize("stored", ["current", "1.0"])
def test_batch_turn_sends_the_snapshot_prompt_and_records_its_hash(stored):
    model = "deepseek/deepseek-v4.1-flash:batch"
    runtime_snapshot = {
        "schema_version": "1.0",
        "provider": "fixture",
        "models": {
            model: {"prompt_rate": "0", "completion_rate": "0", "context_length": 1_000_000}
        },
    }
    config = {
        "schema_version": "1.0",
        "runtime": "openrouter_batch",
        "judge_enabled": False,
        "finder_model": model,
        "validator_model": model,
        "connectors": ["duckduckgo", "web_fetch"],
    }
    with patch("discovery.runtime.check_configuration", return_value=runtime_snapshot):
        row = start_discovery("Find a services business", config, enqueue=False)
    if stored == "1.0":
        row.prompt_snapshot = fx.PROMPTS
        row.save(update_fields=["prompt_snapshot"])
    expected = row.prompt_snapshot["finder"]
    task = claim_task(row.tasks.get(state="open").pk)
    assert task is not None
    with patch("discovery.runtime.advance", side_effect=_Halt), pytest.raises(_Halt):
        loop._execute(task)
    task.refresh_from_db()
    task.turn.refresh_from_db()
    assert task.turn.prompt_hash == expected["hash"]
    assert task.context["history"][0] == {"role": "system", "content": expected["content"]}


@pytest.mark.parametrize("snapshot", [fx.PROMPTS, fx.PROMPTS_1_1], ids=["1.0", "1.1"])
def test_claude_prompt_file_holds_the_snapshot_content(tmp_path, snapshot):
    config = DiscoveryConfig()
    discovery = SimpleNamespace(
        pk=uuid4(),
        config_snapshot=config.model_dump(mode="json"),
        runtime_snapshot={
            "claude_bin": "/fixture/claude",
            "profiles": {
                "judge": {
                    "provider": "ollama",
                    "model": "kimi-k3:cloud",
                    "ollama_bin": "/fixture/ollama",
                }
            },
        },
        prompt_snapshot=snapshot,
    )
    bridge = SimpleNamespace(
        url="http://127.0.0.1:1/mcp", token="synthetic", tools=loop.tools_for_role("judge")
    )
    task = SimpleNamespace(pk=uuid4(), role="judge", discovery=discovery, context={})
    argv, _, _ = build_launch(task, tmp_path, bridge, None, Decimal("1"))
    written = Path(argv[argv.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
    assert written.startswith(snapshot["judge"]["content"] + "\n")


@pytest.mark.django_db
def test_benchmark_dry_run_prints_effective_hashes():
    out = StringIO()
    call_command("run_discovery_benchmark", "--tag", "hashes", stdout=out)
    for role in ROLES:
        assert f"  {role}: {role_prompt(role).hash}" in out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("snapshot", [fx.PROMPTS, fx.PROMPTS_1_1], ids=["1.0", "1.1"])
def test_metrics_read_the_stored_hash_of_either_snapshot_shape(snapshot):
    row = fx.discovery(prompt_snapshot=snapshot)
    assert compute_quality(row).prompt_hashes == {
        role: entry["hash"] for role, entry in snapshot.items()
    }


@pytest.mark.parametrize("role", ROLES)
def test_effective_prompt_is_within_the_word_budget(role):
    _within_budget(role_prompt(role).content)


def test_budget_check_rejects_an_oversized_prompt():
    with pytest.raises(AssertionError, match="budget is 2400"):
        _within_budget("word " * (WORD_BUDGET + 1))
