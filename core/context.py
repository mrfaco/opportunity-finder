"""Context engineering primitives.

The investigation agent loop is the only place these matter in v1, but they are
named and isolated as a module because context engineering is a discipline
worth keeping visible. Helpers here cover:

* token estimation (so we can apply budgets without an API round-trip),
* compaction (drop or summarize older turns when approaching limits),
* just-in-time summarization (replace a verbose tool result with a brief).

All bodies are stubs — see TODOs.
"""

from __future__ import annotations

from typing import Any


def estimate_tokens(text: str, model: str) -> int:
    """Rough token estimate.

    Used by the orchestrator to decide whether to compact history *before*
    making the next model call (cheaper than waiting for the API to reject).

    TODO(v1-followup): replace the 4-chars-per-token heuristic with the
    Anthropic tokenizer or a model-specific calibration. For now this is
    only used for budgeting heuristics, not for billing.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def compact_message_history(
    messages: list[dict[str, Any]],
    target_tokens: int,
) -> list[dict[str, Any]]:
    """Trim or summarize older messages until under ``target_tokens``.

    Strategy outline (to implement):
      1. Always keep the system prompt and the most recent N turns verbatim.
      2. For older turns, replace verbose tool results with their summaries
         (the ``tool_output_summary`` field from ``AgentStep``).
      3. If still over budget, collapse runs of older turns into a single
         "earlier you did X, Y, Z" recap message.

    TODO(v1-followup): implement once the agent loop is wired up.
    """
    return messages


def summarize_for_history(content: str, max_tokens: int) -> str:
    """Produce a compact summary suitable for embedding in conversation history.

    TODO(v1-followup): implement with a Haiku call. Current placeholder
    returns the original content unchanged so the loop control flow can run.
    """
    return content
