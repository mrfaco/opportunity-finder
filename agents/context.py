"""Per-run context-budget bookkeeping for the agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ContextBudget:
    """Running totals consumed by the agent loop.

    Numbers map 1:1 with what we record on each ``AgentStep`` so the loop's
    in-memory state and the persisted state never drift.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    steps: int = 0
    duration_s: float = 0.0

    soft_input_token_limit: int = 150_000  # signal to compact, not to abort
    hard_step_limit: int | None = None
    hard_cost_limit_usd: float | None = None
    hard_duration_limit_s: float | None = None

    def remaining_steps(self) -> int | None:
        if self.hard_step_limit is None:
            return None
        return max(0, self.hard_step_limit - self.steps)

    def remaining_cost(self) -> float | None:
        if self.hard_cost_limit_usd is None:
            return None
        return max(0.0, self.hard_cost_limit_usd - self.cost_usd)

    def remaining_duration(self) -> float | None:
        if self.hard_duration_limit_s is None:
            return None
        return max(0.0, self.hard_duration_limit_s - self.duration_s)

    def is_exhausted(self) -> str | None:
        """Return a termination reason if any hard limit is breached, else None."""
        if self.hard_step_limit is not None and self.steps >= self.hard_step_limit:
            return "budget_steps"
        if self.hard_cost_limit_usd is not None and self.cost_usd >= self.hard_cost_limit_usd:
            return "budget_cost"
        if self.hard_duration_limit_s is not None and self.duration_s >= self.hard_duration_limit_s:
            return "budget_duration"
        return None


def should_compact(history: list[dict[str, Any]], budget: ContextBudget) -> bool:
    """Heuristic check — are we approaching the soft input-token limit?

    TODO(v1-followup): refine once the agent loop actually runs end-to-end.
    """
    return budget.input_tokens >= budget.soft_input_token_limit


def inject_budget_status(
    history: list[dict[str, Any]],
    steps_remaining: int | None,
    cost_remaining: float | None,
    duration_remaining: float | None,
) -> list[dict[str, Any]]:
    """Append a system-style note nudging the agent toward termination.

    TODO(v1-followup): tune the message wording with the prompt-authoring
    session. For now this is a placeholder that demonstrates where the
    budget-status hint lives in the conversation history.
    """
    parts = []
    if steps_remaining is not None:
        parts.append(f"{steps_remaining} steps remaining")
    if cost_remaining is not None:
        parts.append(f"${cost_remaining:.4f} cost remaining")
    if duration_remaining is not None:
        parts.append(f"{duration_remaining:.0f}s duration remaining")
    if not parts:
        return history
    return [
        *history,
        {"role": "user", "content": f"[budget] {' · '.join(parts)}"},
    ]
