"""Focused regressions for the first live Ollama launch."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import psutil
import pytest

from discovery.agent_tools import AgentTools
from discovery.claude_runtime import _descendants, _stop
from discovery.connectors.web import DuckDuckGoConnector, WebFetchConnector
from discovery.errors import DiscoveryStop


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
