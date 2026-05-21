"""Tool registry.

Tools are registered as side-effects of importing ``agents.tools.stubs``
(see ``AgentsConfig.ready``). The registry is process-local; nothing about
the shape requires a database.

In v1, all tool implementations raise ``NotImplementedError``. The
orchestrator dispatches through ``Tool.dispatch``, so plumbing real
implementations is a per-tool drop-in.
"""

from __future__ import annotations

import hashlib
from typing import Any

from agents.tools.base import Tool, ToolDefinition

TOOL_REGISTRY: dict[str, Tool] = {}


# Agents → toolsets. v1 has only the investigation agent.
AGENT_TOOLSETS: dict[str, list[str]] = {
    "investigation": [
        "query_cluster",
        "query_related_clusters",
        "query_known_competitors",
        "search_hacker_news",
        "fetch_hn_item",
        "search_github_issues",
        "search_stack_overflow",
        "search_product_hunt",
        "fetch_product_hunt_comments",
        "web_search",
        "fetch_url",
        "query_trustmrr",
        "summarize_text",
    ],
}


def register(tool: Tool) -> None:
    if tool.name in TOOL_REGISTRY:
        raise ValueError(f"Tool already registered: {tool.name!r}")
    TOOL_REGISTRY[tool.name] = tool


def get_tool(name: str) -> Tool:
    try:
        return TOOL_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"No tool registered with name {name!r}") from exc


def all_tools_for_agent(agent_name: str) -> list[Tool]:
    names = AGENT_TOOLSETS.get(agent_name, [])
    return [TOOL_REGISTRY[n] for n in names if n in TOOL_REGISTRY]


def export_mcp_definitions(agent_name: str) -> list[dict[str, Any]]:
    """Return tool definitions in MCP server format.

    The shape is what an MCP server would publish: ``{name, description,
    inputSchema}``. Keeping the export validates that every registered
    tool's Pydantic models can produce JSON Schema.
    """
    out: list[dict[str, Any]] = []
    for tool in all_tools_for_agent(agent_name):
        out.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema(),
            }
        )
    return out


def registry_hash() -> str:
    """sha256 of the sorted tool names — used in the run config snapshot."""
    names = ",".join(sorted(TOOL_REGISTRY.keys()))
    return hashlib.sha256(names.encode("utf-8")).hexdigest()


__all__ = [
    "TOOL_REGISTRY",
    "Tool",
    "ToolDefinition",
    "register",
    "get_tool",
    "all_tools_for_agent",
    "export_mcp_definitions",
    "registry_hash",
]
