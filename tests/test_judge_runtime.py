"""Judge tool isolation and launch routing without any model requests."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from discovery.claude_runtime import build_launch
from discovery.loop import tools_for_role
from discovery.schemas import DiscoveryConfig


def test_judge_has_only_research_and_judgment_tools():
    tools = tools_for_role("judge")
    assert [tool["function"]["name"] for tool in tools] == ["research", "submit_judgment"]
    properties = tools[1]["function"]["parameters"]["properties"]
    assert "reviewed_proposal_hash" in properties
    assert "reviewed_validation_hash" in properties
    assert "assessment" in properties
    assert "worth_pursuing" in properties


def test_ollama_judge_launch_pins_kimi_and_uses_a_fresh_session(tmp_path):
    config = DiscoveryConfig(judge_model="kimi-k3:cloud")
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
        prompt_snapshot={"judge": {"content": "Synthetic Judge prompt"}},
    )
    bridge = SimpleNamespace(
        url="http://127.0.0.1:1/mcp", token="synthetic-capability", tools=tools_for_role("judge")
    )
    sessions = []
    for _ in range(2):
        task = SimpleNamespace(pk=uuid4(), role="judge", discovery=discovery)
        argv, environment, session = build_launch(task, tmp_path, bridge, None, Decimal("1"))
        assert argv[:7] == [
            "/fixture/ollama",
            "launch",
            "claude",
            "--model",
            "kimi-k3:cloud",
            "--yes",
            "--",
        ]
        assert argv.count("--model") == 1
        assert argv[argv.index("--allowedTools") + 1] == (
            "mcp__discovery__research,mcp__discovery__submit_judgment"
        )
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert environment["CLAUDE_CODE_SUBAGENT_MODEL"] == "kimi-k3:cloud"
        sessions.append(session)
    assert sessions[0] != sessions[1]
