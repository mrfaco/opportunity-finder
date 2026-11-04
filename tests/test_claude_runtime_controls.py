"""Focused regressions for the first live Ollama launch."""

import json
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import psutil
import pytest
from django.utils import timezone

from discovery.agent_tools import AgentTools
from discovery.claude_runtime import _descendants, _run_process, _stop
from discovery.connectors.web import DuckDuckGoConnector, WebFetchConnector
from discovery.errors import DiscoveryStop
from discovery.models import Discovery, DiscoveryTask, DiscoveryTurn, Evidence, ResearchOperation
from discovery.orchestrator import finish_discovery
from discovery.research import begin_operation, research_calls_used
from discovery.termination import SubmissionError, has_independent_research, validate_references


@pytest.mark.django_db
def test_unknown_citation_gets_a_full_id_hint_but_is_not_silently_accepted():
    discovery = Discovery.objects.create(objective="citations", request_hash="citations")
    source = Evidence.objects.create(
        discovery=discovery,
        url="https://source.example/",
        title="Original source",
        retrieved_at=timezone.now(),
    )
    with pytest.raises(SubmissionError) as failure:
        validate_references(discovery, {"evidence_ids": [str(source.pk)[:8]]})
    assert str(source.pk) in str(failure.value)
    assert "Original source" in str(failure.value)
    validate_references(discovery, {"evidence_ids": [str(source.pk)]})


@pytest.mark.django_db
def test_local_cooldown_denial_preserves_real_research_slot():
    discovery = Discovery.objects.create(
        objective="quota",
        request_hash="quota",
        config_snapshot={"schema_version": "1.1", "max_research_calls_per_turn": 1},
    )
    task = DiscoveryTask.objects.create(
        discovery=discovery,
        role="validator",
        turn_number=1,
        state="working",
        lease_token=uuid4(),
        lease_expires_at=timezone.now() + timedelta(minutes=3),
    )
    DiscoveryTurn.objects.create(discovery=discovery, task=task, role="validator", number=1)
    ResearchOperation.objects.create(
        discovery=discovery,
        task=task,
        kind="research",
        operation_key="local-denial",
        state="completed",
        response={"request_performed": False, "status": "rate_limited"},
    )
    assert research_calls_used(task) == 0
    begin_operation(task, "real-fetch", "research", {"action": "fetch"}, Decimal("0"))
    assert research_calls_used(task) == 1
    with pytest.raises(DiscoveryStop, match="Maximum research calls reached"):
        begin_operation(task, "extra-fetch", "research", {"action": "fetch"}, Decimal("0"))


def test_explicit_recovery_keeps_original_wake_logs(tmp_path):
    task = SimpleNamespace(
        pk=uuid4(), lease_token=uuid4(), context={"claude_recovery_id": str(uuid4())}
    )
    original = tmp_path / f"{task.pk}.jsonl"
    original.write_text("original paid wake audit")
    with (
        patch("discovery.claude_runtime.renew_task"),
        patch("discovery.claude_runtime.check_limits"),
    ):
        result = _run_process(
            task,
            [
                sys.executable,
                "-c",
                'print(\'{"type":"result","subtype":"success","is_error":false}\')',
            ],
            {},
            tmp_path,
            SimpleNamespace(failure=None),
            None,
            {},
        )
    assert result["subtype"] == "success"
    assert original.read_text() == "original paid wake audit"
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_invalid_immutable_proposal_routes_validator_back_to_finder():
    with AgentTools(SimpleNamespace(), []) as bridge:
        with patch.object(
            bridge, "call", side_effect=SubmissionError("Two distinct source origins are required")
        ):
            response = bridge.handle(
                {"method": "tools/call", "params": {"name": "submit_validation"}}
            )
        result = json.loads(response["content"][0]["text"])
        assert result["status"] == "validation_failed"
        assert "Submit a challenge" in result["recovery_hint"]
        assert bridge.failure is None


@pytest.mark.django_db
def test_recovered_run_can_record_a_different_terminal_outcome():
    discovery = Discovery.objects.create(objective="recovered lifecycle", request_hash="test")
    finish_discovery(discovery.pk, "invalid_submission", status="failed")
    Discovery.objects.filter(pk=discovery.pk).update(status="running", ended_at=None)
    result = finish_discovery(discovery.pk, "turn_limit", status="completed")
    assert result.status == "completed"
    assert result.messages.filter(sender="system", recipient="human").count() == 2
    finish_discovery(discovery.pk, "turn_limit", status="completed")
    assert result.messages.filter(sender="system", recipient="human").count() == 2


@pytest.mark.django_db
def test_independent_fetch_allows_challenge_but_never_acceptance():
    discovery = Discovery.objects.create(objective="independent review", request_hash="test")
    finder = DiscoveryTask.objects.create(
        discovery=discovery, role="finder", turn_number=1, state="done"
    )
    turn = DiscoveryTurn.objects.create(discovery=discovery, task=finder, role="finder", number=1)
    Evidence.objects.create(
        discovery=discovery, turn=turn, url="https://known.example/", retrieved_at=timezone.now()
    )
    validator = DiscoveryTask.objects.create(discovery=discovery, role="validator", turn_number=2)
    op = ResearchOperation.objects.create(
        discovery=discovery,
        task=validator,
        kind="research",
        state="completed",
        operation_key="fetch",
        request={"action": "fetch"},
        response={
            "status": "success",
            "documents": [
                {
                    "url": "https://new.example/",
                    "kind": "fetched_page",
                    "text": "Independent counterevidence",
                }
            ],
        },
    )
    assert has_independent_research(validator, require_search=False)
    assert not has_independent_research(validator)
    op.response["documents"][0]["url"] = "https://known.example/"
    op.save()
    assert not has_independent_research(validator, require_search=False)


@pytest.mark.parametrize("status,body", [(202, "challenge"), (200, "anomaly challenge")])
def test_search_challenge_is_visible_and_does_not_abort_agent(status, body):
    response = httpx.Response(status, text=body)
    with patch("discovery.connectors.web.httpx.get", return_value=response):
        result = DuckDuckGoConnector().search("Argentine cybersecurity")
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 60
    assert not result.documents
    with AgentTools(SimpleNamespace(), []) as bridge:
        with patch.object(bridge, "call", return_value=result.model_dump(mode="json")):
            message = bridge.handle({"method": "tools/call", "params": {"name": "research"}})
        assert json.loads(message["content"][0]["text"])["status"] == "rate_limited"
        assert bridge.failure is None


def test_slow_source_is_a_visible_tool_outcome():
    with patch("discovery.connectors.web.fetch_public_url", side_effect=TimeoutError):
        result = WebFetchConnector().fetch("https://example.com/slow")
    assert result.status == "timeout"
    assert result.error_reason
    assert not result.documents


def test_research_quota_allows_terminal_submission():
    with AgentTools(SimpleNamespace(), []) as bridge:
        with patch.object(bridge, "call", side_effect=DiscoveryStop("turn_call_limit")):
            result = bridge.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {"name": "research", "arguments": {}},
                }
            )
        response = json.loads(result["content"][0]["text"])
        assert response["status"] == "research_limit_reached"
        assert bridge.failure is None
        with patch.object(bridge, "call", return_value={"status": "success"}):
            submitted = bridge.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "submit_proposal", "arguments": {}},
                }
            )
        assert json.loads(submitted["content"][0]["text"])["status"] == "success"


def test_cleanup_never_signals_process_groups_or_unrelated_processes():
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",'
            '"import time; time.sleep(30)"],start_new_session=True); '
            "print(p.pid,flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    child = psutil.Process(int(process.stdout.readline()))
    try:
        family = _descendants(process.pid)
        with patch("os.killpg", side_effect=PermissionError("protected process group")):
            _stop(process, family)
        assert process.poll() is not None
        assert sentinel.poll() is None
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        for item in (process, sentinel):
            if item.poll() is None:
                item.kill()
            item.wait()
        if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
            child.kill()
