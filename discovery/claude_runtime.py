"""Video Swarm-style Claude Code wakes with explicit per-role provider routing."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import psutil
from django.conf import settings
from django.db import close_old_connections

from discovery.agent_tools import AgentTools
from discovery.errors import DiscoveryError, DiscoveryStop, OutcomeUnknown
from discovery.models import Discovery, DiscoveryTask, ResearchOperation
from discovery.orchestrator import commit_turn, renew_task
from discovery.provider_gateway import Gateway
from discovery.research import (
    begin_operation,
    check_limits,
    complete_operation,
    lease_token,
)
from discovery.schemas import DiscoveryConfig


def _environment() -> dict[str, str]:
    result = {
        name: os.environ[name] for name in ("HOME", "USER", "LANG", "TMPDIR") if name in os.environ
    }
    result.update(
        {
            "PATH": os.pathsep.join(
                [
                    str(Path(settings.BASE_DIR) / ".venv/bin"),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                ]
            ),
            "SHELL": "/bin/zsh",
            "LANG": result.get("LANG", "en_US.UTF-8"),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "ENABLE_TOOL_SEARCH": "false",
            "MCP_CONNECTION_NONBLOCKING": "0",
            "MCP_DISCOVERY_CACHE": "0",
            "CLAUDE_CODE_MAX_RETRIES": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    return result


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ValueError(f"{name} is not installed on the agent worker host")
    return path


def _version(binary: str) -> str:
    result = subprocess.run(
        [binary, "--version"],
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def _provider_metadata(url: str, label: str) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=20)
    except httpx.HTTPError as exc:
        raise ValueError(f"{label} is unavailable on the configured worker") from exc
    if not response.is_success:
        raise ValueError(f"{label} metadata returned HTTP {response.status_code}")
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError(f"{label} returned invalid metadata")
    return document


def check_configuration(config: DiscoveryConfig) -> dict[str, Any]:
    claude = _binary("claude")
    version = _version(claude)
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    if not match or not (2, 1, 311) <= tuple(map(int, match.groups())) < (2, 2, 0):
        raise ValueError("Claude Code 2.1.311–2.1.x is required, matching Video Swarm")
    # Managed routing overrides take precedence over CLI setting-sources.
    for candidate in (
        "/Library/Application Support/ClaudeCode/managed-settings.json",
        "/etc/claude-code/managed-settings.json",
    ):
        path = Path(candidate)
        if path.exists():
            document = json.loads(path.read_text())
            if any(
                str(k).startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))
                for k in document.get("env", {})
            ) or set(document) & {"apiKeyHelper", "model", "modelOverrides", "modelSettings"}:
                raise ValueError(
                    "Managed Claude routing conflicts with explicit provider selection"
                )
    profiles: dict[str, Any] = {}
    for role in ("finder", "validator"):
        provider = getattr(config, f"{role}_provider")
        model = getattr(config, f"{role}_model")
        profile: dict[str, Any] = {"provider": provider, "model": model}
        if provider == "openrouter":
            if not settings.OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY is required for the selected role")
            metadata = _provider_metadata(
                f"https://openrouter.ai/api/v1/models/{model}/endpoints", "OpenRouter"
            )
            endpoints = [
                e
                for e in metadata["data"]["endpoints"]
                if "tools" in e.get("supported_parameters", [])
            ]
            if not endpoints:
                raise ValueError(f"{model} has no tool-capable OpenRouter endpoints")
            profile.update(
                {
                    "prompt_rate": str(max(Decimal(e["pricing"]["prompt"]) for e in endpoints)),
                    "completion_rate": str(
                        max(Decimal(e["pricing"]["completion"]) for e in endpoints)
                    ),
                }
            )
        elif provider == "anthropic":
            if not settings.ANTHROPIC_API_KEY:
                auth = subprocess.run(
                    [claude, "auth", "status", "--json"],
                    env=_environment(),
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if auth.returncode or not json.loads(auth.stdout).get("loggedIn"):
                    raise ValueError(
                        "Anthropic needs ANTHROPIC_API_KEY or a Claude Code login on the worker"
                    )
            profile["accounting"] = (
                "Claude Code reported usage; subscription valuation is estimated"
            )
        else:
            profile["ollama_bin"] = _binary("ollama")
            profile["ollama_version"] = _version(profile["ollama_bin"])
            ollama_version = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", profile["ollama_version"])
            if not ollama_version or not (0, 40, 11) <= tuple(map(int, ollama_version.groups())) < (
                0,
                41,
                0,
            ):
                raise ValueError("Ollama 0.40.11–0.40.x is required, matching Video Swarm")
            if not model.endswith(":cloud"):
                metadata = _provider_metadata("http://127.0.0.1:11434/api/tags", "Ollama")
                installed = {m["name"] for m in metadata.get("models", [])}
                if model not in installed and model + ":latest" not in installed:
                    raise ValueError(
                        "Select an installed Ollama model; automatic model downloads are disabled"
                    )
            profile["accounting"] = (
                "Local inference has no per-token API bill; cloud-plan charges unverified"
            )
        profiles[role] = profile
    return {
        "schema_version": "1.1",
        "runtime": "claude_code",
        "claude_bin": claude,
        "claude_version": version,
        "profiles": profiles,
    }


def _private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def build_launch(
    task: DiscoveryTask,
    run_dir: Path,
    bridge: AgentTools,
    gateway: Gateway | None,
    max_model_cost: Decimal,
) -> tuple[list[str], dict[str, str], str]:
    config = DiscoveryConfig.model_validate(task.discovery.config_snapshot)
    profile = task.discovery.runtime_snapshot["profiles"][task.role]
    provider, model = profile["provider"], profile["model"]
    claude = task.discovery.runtime_snapshot["claude_bin"]
    env = _environment()
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(config.max_output_tokens)
    native = "claude-opus-4-6" if provider == "openrouter" else model
    if gateway is not None:
        env.update(
            {
                "ANTHROPIC_BASE_URL": gateway.url,
                "ANTHROPIC_AUTH_TOKEN": gateway.token,
                "ANTHROPIC_API_KEY": "",
                "CLAUDE_CONFIG_DIR": str(run_dir / "claude-config"),
            }
        )
    elif provider == "anthropic" and settings.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY
    for tier in ("OPUS", "SONNET", "HAIKU", "FABLE"):
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = native
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = native
    mcp = run_dir / f"mcp-{task.pk}.json"
    _private_write(
        mcp,
        json.dumps(
            {
                "mcpServers": {
                    "discovery": {
                        "type": "http",
                        "url": bridge.url,
                        "headers": {"Authorization": f"Bearer {bridge.token}"},
                    }
                }
            }
        ),
    )
    prompt = run_dir / f"prompt-{task.pk}.md"
    _private_write(
        prompt,
        task.discovery.prompt_snapshot[task.role]["content"]
        + "\nUse only discovery MCP tools. After a successful submission, end immediately.\n",
    )
    session = (
        str(uuid.uuid5(task.discovery.pk, "finder")) if task.role == "finder" else str(uuid.uuid4())
    )
    config_dir = Path(env.get("CLAUDE_CONFIG_DIR", str(Path(env["HOME"]) / ".claude")))
    project_name = re.sub(r"[^A-Za-z0-9]", "-", str(run_dir.resolve()))
    transcript = config_dir / "projects" / project_name / f"{session}.jsonl"
    argv = [
        claude,
        "-p",
        "--system-prompt-file",
        str(prompt),
        "--resume" if transcript.exists() else "--session-id",
        session,
        "--model",
        native,
        "--tools",
        "",
        "--allowedTools",
        ",".join("mcp__discovery__" + t["function"]["name"] for t in bridge.tools),
        "--permission-mode",
        "dontAsk",
        "--restricted",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp),
        "--disable-slash-commands",
        "--no-chrome",
        "--max-turns",
        str(config.max_model_calls_per_turn),
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if provider == "openrouter":
        argv += ["--settings", json.dumps({"modelOverrides": {native: model}})]
    if provider == "anthropic":
        argv += ["--max-budget-usd", str(max_model_cost)]
    if provider == "ollama":
        # Ollama injects Claude's model argument itself. Keep the exact model
        # only on the launcher, as in Video Swarm, rather than passing it twice.
        model_index = argv.index("--model")
        del argv[model_index : model_index + 2]
        scratch = run_dir / "ollama-home"
        shim_dir = run_dir / "ollama-bin"
        for path in (scratch, shim_dir):
            path.mkdir(exist_ok=True, mode=0o700)
        shim = shim_dir / "claude"
        _private_write(
            shim,
            f'#!/bin/sh\nexport HOME={shlex.quote(env["HOME"])}\nexec {shlex.quote(claude)} "$@"\n',
        )
        shim.chmod(0o700)
        env["HOME"] = str(scratch)
        env["PATH"] = str(shim_dir) + os.pathsep + env["PATH"]
        env["OLLAMA_HOST"] = "http://127.0.0.1:11434"
        argv = [
            profile["ollama_bin"],
            "launch",
            "claude",
            "--model",
            model,
            "--yes",
            "--",
            *argv[1:],
        ]
    return argv, env, session


def _descendants(pid: int) -> set[psutil.Process]:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:  # allow: suppress-exception
        return set()  # A process that already exited needs no cleanup.
    return {root, *root.children(recursive=True)}


def _stop(process: subprocess.Popen[bytes], children: set[psutil.Process]) -> None:
    # Process objects retain creation times, so PID reuse cannot turn a former
    # child into an unrelated cleanup target. Never signal whole process groups:
    # Ollama can inherit groups containing protected macOS helpers.
    children |= _descendants(process.pid)
    owned = []
    for child in children:
        try:
            identities = child.uids()
            if (
                child.is_running()
                and identities.real == os.getuid()
                and identities.effective == os.geteuid()
            ):
                owned.append(child)
        except psutil.NoSuchProcess:  # allow: suppress-exception
            continue  # The descendant already exited.
    for child in sorted(owned, key=lambda p: p.pid == process.pid):
        try:
            child.terminate()
        except psutil.NoSuchProcess:  # allow: suppress-exception
            continue  # A sibling's termination may have reaped this child.
    _, alive = psutil.wait_procs([child for child in owned if child.pid != process.pid], timeout=1)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:  # allow: suppress-exception
            continue
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:  # allow: suppress-exception
        # The direct Popen child must be reaped by Popen to preserve its exit status.
        process.kill()
        process.wait(timeout=5)


def _result(path: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    results = [event for event in events if event.get("type") == "result"]
    if not results:
        raise DiscoveryError(
            "Claude Code exited without a structured result; inspect private wake logs"
        )
    return results[-1]


def _run_process(
    task: DiscoveryTask,
    argv: list[str],
    env: dict[str, str],
    run_dir: Path,
    bridge: AgentTools,
    gateway: Gateway | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    input_path, output_path, error_path = [
        run_dir / f"{task.pk}.{suffix}" for suffix in ("input.json", "jsonl", "stderr")
    ]
    _private_write(input_path, json.dumps(inputs))
    for path in (output_path, error_path):
        _private_write(path, "")
    children: set[psutil.Process] = set()
    with (
        input_path.open("rb") as source,
        output_path.open("wb") as output,
        error_path.open("wb") as error,
    ):
        process = subprocess.Popen(
            argv,
            cwd=run_dir,
            env=env,
            stdin=source,
            stdout=output,
            stderr=error,
            start_new_session=True,
        )
        try:
            last_renewal = 0.0
            while process.poll() is None:
                if time.monotonic() - last_renewal >= 5:
                    renew_task(task.pk, lease_token(task))
                    check_limits(task)
                    last_renewal = time.monotonic()
                children |= _descendants(process.pid)
                if bridge.failure is not None:
                    raise bridge.failure
                if gateway is not None and gateway.failure is not None:
                    raise gateway.failure
                if max(output_path.stat().st_size, error_path.stat().st_size) > 16_000_000:
                    raise DiscoveryError("Claude wake log size limit exceeded")
                time.sleep(0.25)
        finally:
            _stop(process, children)
    if bridge.failure is not None:
        raise bridge.failure
    if gateway is not None and gateway.failure is not None:
        raise gateway.failure
    result = _result(output_path)
    if (process.returncode or result.get("is_error")) and result.get(
        "subtype"
    ) != "error_max_turns":
        raise DiscoveryError(f"Claude Code stopped with {result.get('subtype', 'provider_error')}")
    return result


def run_turn(task: DiscoveryTask, inputs: dict[str, Any], tools: list[dict[str, Any]]) -> None:
    config = DiscoveryConfig.model_validate(task.discovery.config_snapshot)
    profile = task.discovery.runtime_snapshot["profiles"][task.role]
    run_dir = Path(settings.BASE_DIR) / ".tmp" / "claude-runs" / str(task.discovery_id)
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    task.turn.input_snapshot = inputs
    task.turn.save(update_fields=["input_snapshot"])
    discovery = Discovery.objects.get(pk=task.discovery_id)
    remaining = config.max_cost_usd - discovery.cost_used_usd
    research_hold = (
        max(
            (Decimal(d["search_cost_upper_bound_usd"]) for d in discovery.connector_snapshot),
            default=Decimal("0"),
        )
        * config.max_research_calls_per_turn
    )
    model_budget = remaining - research_hold
    if model_budget <= 0:
        raise DiscoveryStop(
            "cost_limit", "Budget cannot reserve this turn's model and research calls"
        )
    wake = begin_operation(
        task,
        f"{task.pk}:claude:wake",
        "agent_wake",
        {"provider": profile["provider"], "model": profile["model"]},
        model_budget if profile["provider"] == "anthropic" else Decimal("0"),
    )
    if wake.state != "planned":
        raise OutcomeUnknown(
            "A previous Claude wake was interrupted; do not repeat paid work automatically"
        )
    wake.state = "started"
    wake.save(update_fields=["state"])

    def before_call(body: dict[str, Any]) -> ResearchOperation:
        close_old_connections()
        if not body.get("tools"):
            raise DiscoveryError("Claude Code did not expose discovery MCP tools; request refused")
        maximum = min(body["max_tokens"], config.max_output_tokens)
        body["max_tokens"] = maximum
        bound = len(json.dumps(body, ensure_ascii=False).encode()) + 8192
        amount = Decimal(bound) * Decimal(profile["prompt_rate"]) + Decimal(maximum) * Decimal(
            profile["completion_rate"]
        )
        index = task.operations.filter(kind="model").count()
        operation = begin_operation(
            task,
            f"{task.pk}:claude:model:{index}",
            "model",
            {"body": body},
            amount.quantize(Decimal("0.00000001")),
        )
        operation.state = "started"
        operation.save(update_fields=["state"])
        close_old_connections()
        return operation

    def after_call(operation: ResearchOperation, observed: dict[str, Any]) -> None:
        close_old_connections()
        complete_operation(task, operation, observed)
        close_old_connections()

    def on_error(operation: ResearchOperation, message: str, unknown: bool) -> None:
        close_old_connections()
        operation.state = "unknown" if unknown else "failed"
        operation.error_summary = message
        operation.save(update_fields=["state", "error_summary"])
        close_old_connections()

    with ExitStack() as stack:
        bridge = stack.enter_context(AgentTools(task, tools))
        gateway = (
            stack.enter_context(
                Gateway(
                    settings.OPENROUTER_API_KEY,
                    profile["model"],
                    before_call,
                    after_call,
                    on_error,
                    prompt_rate=Decimal(profile["prompt_rate"]),
                    completion_rate=Decimal(profile["completion_rate"]),
                    allowed_tools=frozenset(
                        "mcp__discovery__" + t["function"]["name"] for t in tools
                    ),
                    max_output_tokens=config.max_output_tokens,
                )
            )
            if profile["provider"] == "openrouter"
            else None
        )
        argv, env, session = build_launch(task, run_dir, bridge, gateway, model_budget)
        task.context = {
            **task.context,
            "claude_session_id": session,
            "claude_log_dir": str(run_dir),
        }
        task.save(update_fields=["context"])
        result = _run_process(task, argv, env, run_dir, bridge, gateway, inputs)
    task.refresh_from_db(fields=["context"])
    usage = result.get("usage", {})
    summary = {
        name: result.get(name)
        for name in ("subtype", "session_id", "num_turns", "total_cost_usd", "modelUsage")
    }
    if profile["provider"] == "anthropic" and "total_cost_usd" not in result:
        raise OutcomeUnknown("Claude Code omitted cost accounting for its Anthropic wake")
    cost = (
        Decimal(str(result["total_cost_usd"]))
        if profile["provider"] == "anthropic"
        else Decimal("0")
    )
    complete_operation(
        task,
        wake,
        {
            "schema_version": "1.0",
            "result": summary,
            "cost_usd": str(cost),
            "cost_is_estimate": profile["provider"] != "openrouter",
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "prompt_tokens_details": {"cached_tokens": usage.get("cache_read_input_tokens", 0)},
            },
            "accounting_note": profile.get("accounting", "Per-request gateway ledger"),
            "cloud_charges_unverified": profile["provider"] == "ollama"
            and profile["model"].endswith(":cloud"),
        },
    )
    submission = task.context.get("claude_submission")
    if submission is None:
        raise DiscoveryStop(
            "turn_call_limit"
            if result.get("subtype") == "error_max_turns"
            else "invalid_submission",
            "Claude exited without submitting its role's brief",
        )
    commit_turn(
        task.pk,
        lease_token(task),
        submission,
        model=profile["model"],
        prompt_hash=task.discovery.prompt_snapshot[task.role]["hash"],
    )
