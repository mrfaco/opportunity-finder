"""Investigation agent loop — control-flow skeleton.

The loop is intentionally split from the orchestrator. The orchestrator owns
persistence + budget enforcement + tool dispatch + trajectory logging; the
loop's only job is to consume snapshots, drive the model, and decide when to
emit a final brief.

In v1, the model call (`_call_model`) and most tool implementations raise
``NotImplementedError``. The control flow itself is complete so the wiring
is testable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from agents import context as ctx
from agents import cost
from agents import tools as tool_registry
from agents.cache import RunScopedCache
from agents.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    EventType,
    StepType,
    TerminationReason,
    ToolStatus,
)

logger = logging.getLogger(__name__)

MAX_STEPS_SANITY = 1000


class LoopAbort(Exception):
    """Raised internally when the loop should terminate. Carries the reason."""

    def __init__(self, reason: TerminationReason, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _build_initial_history(run: AgentRun) -> list[dict[str, Any]]:
    """Construct the model's starting conversation from the run snapshot.

    Includes the system + procedural prompts and a structured "here's your
    cluster" first user turn. Just-in-time loading: only the cluster summary
    is included up front; the agent fetches full member content via
    ``query_cluster`` if it needs detail.
    """
    snapshot_prompts = run.config_snapshot.get("prompts", {})
    history: list[dict[str, Any]] = []
    system = snapshot_prompts.get("system", {}).get("content")
    procedural = snapshot_prompts.get("procedural", {}).get("content")
    if system:
        history.append({"role": "system", "content": system})
    if procedural:
        history.append({"role": "user", "content": procedural})
    cluster = run.cluster_snapshot
    history.append(
        {
            "role": "user",
            "content": (
                f"Investigate cluster {cluster['id']}.\n"
                f"Title: {cluster.get('title') or '(none yet)'}\n"
                f"Summary: {cluster.get('summary') or '(none yet)'}\n"
                f"Size: {cluster.get('size')} · Sources: {cluster.get('sources')}\n"
                "Call query_cluster first if you need the member items."
            ),
        }
    )
    return history


def _call_model(
    history: list[dict[str, Any]], model: str, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Single Anthropic completion call.

    Expected return shape:
        {
            "content": [...],         # raw content blocks
            "stop_reason": "...",
            "input_tokens": int,
            "output_tokens": int,
            "cached_tokens": int,
            "tool_calls": [...],      # parsed tool-use blocks
            "final_text": str | None, # text content if model is done
        }

    TODO(v1-followup): implement against the Anthropic SDK with prompt
    caching enabled (cache the system + procedural prompts; tools are tier-1
    cached as well). Plumb errors → ``ToolStatus`` / loop termination paths.
    """
    raise NotImplementedError("TODO(v1-followup): wire up Anthropic SDK call with prompt caching")


def _dispatch_tool(
    tool_name: str, tool_input: dict[str, Any], cache: RunScopedCache
) -> tuple[dict[str, Any], bool, int | None]:
    """Dispatch one tool call, consulting the run-scoped cache first.

    Returns ``(output_dict, was_cached, cache_age_seconds)``.
    """
    input_blob = json.dumps(tool_input, sort_keys=True)
    input_hash = hashlib.sha256(input_blob.encode("utf-8")).hexdigest()[:16]

    cached = cache.get(tool_name, input_hash)
    if cached is not None:
        return cached, True, None  # TTL exposes age only if we tracked it; v2.

    tool = tool_registry.get_tool(tool_name)
    output_model = tool.dispatch(tool_input)
    output = output_model.model_dump()

    if tool.cache_ttl_seconds and output.get("status") == "success":
        cache.set(tool_name, input_hash, output, ttl_seconds=tool.cache_ttl_seconds)
    return output, False, None


def _summarize_tool_output(output: dict[str, Any], max_chars: int = 400) -> str:
    """Cheap, deterministic summary for the step's ``tool_output_summary``."""
    if "status" not in output:
        return ""
    rendered = str(output)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 3] + "..."


def run_loop(run_id: UUID | str) -> None:
    """Execute the investigation loop for an existing ``AgentRun``."""
    run = AgentRun.objects.select_related("cluster").get(pk=run_id)
    cache = RunScopedCache(run.id)
    budget = ctx.ContextBudget(
        hard_step_limit=run.budget_max_steps,
        hard_cost_limit_usd=float(run.budget_max_cost_usd),
        hard_duration_limit_s=run.budget_max_duration_s,
    )
    history = _build_initial_history(run)
    model_name = run.config_snapshot["model_selection"].get("investigation")
    tool_defs = tool_registry.export_mcp_definitions(run.agent_name)
    start_time = time.monotonic()
    event_seq = 0

    try:
        for step_number in range(1, MAX_STEPS_SANITY + 1):
            # Allow human-initiated kill mid-run.
            run.refresh_from_db(fields=["status"])
            if run.status == AgentRunStatus.KILLED:
                raise LoopAbort(TerminationReason.KILLED_BY_HUMAN)  # noqa: TRY301

            budget.duration_s = time.monotonic() - start_time
            reason = budget.is_exhausted()
            if reason is not None:
                raise LoopAbort(TerminationReason(reason))  # noqa: TRY301

            if ctx.should_compact(history, budget):
                # TODO(v1-followup): replace verbose tool outputs with their
                # summaries, drop stale turns. For now we leave history alone.
                pass

            response = _call_model(history, model_name, tool_defs)

            step_cost = cost.compute_cost(
                model_name,
                response["input_tokens"],
                response["output_tokens"],
                response.get("cached_tokens", 0),
            )
            budget.input_tokens += response["input_tokens"]
            budget.output_tokens += response["output_tokens"]
            budget.cached_tokens += response.get("cached_tokens", 0)
            budget.cost_usd += float(step_cost)
            budget.steps += 1

            step = AgentStep.objects.create(
                run=run,
                step_number=step_number,
                step_type=(
                    StepType.TOOL_CALL
                    if response.get("tool_calls")
                    else StepType.FINAL_OUTPUT
                    if response.get("final_text")
                    else StepType.MODEL_REASONING_ONLY
                ),
                input_tokens=response["input_tokens"],
                output_tokens=response["output_tokens"],
                cached_tokens=response.get("cached_tokens", 0),
                cost_usd=step_cost,
                model=model_name,
                cumulative_cost_usd=Decimal(str(budget.cost_usd)),
                cumulative_steps=budget.steps,
            )
            event_seq += 1
            AgentEvent.objects.create(
                run=run,
                step=step,
                sequence=event_seq,
                event_type=EventType.MODEL_RESPONSE,
                payload=response,
                payload_size_bytes=len(str(response)),
            )

            # Tool calls
            for call in response.get("tool_calls", []):
                tool_name = call["name"]
                tool_input = call["input"]
                event_seq += 1
                AgentEvent.objects.create(
                    run=run,
                    step=step,
                    sequence=event_seq,
                    event_type=EventType.TOOL_REQUEST,
                    payload=call,
                    payload_size_bytes=len(str(call)),
                    tool_name=tool_name,
                )
                output, was_cached, age = _dispatch_tool(tool_name, tool_input, cache)
                step.tool_name = tool_name
                step.tool_input = tool_input
                step.tool_output_summary = _summarize_tool_output(output)
                step.tool_status = ToolStatus(output.get("status", ToolStatus.ERROR))
                step.was_cached = was_cached
                step.cache_age_seconds = age
                step.save(
                    update_fields=[
                        "tool_name",
                        "tool_input",
                        "tool_output_summary",
                        "tool_status",
                        "was_cached",
                        "cache_age_seconds",
                    ]
                )
                event_seq += 1
                AgentEvent.objects.create(
                    run=run,
                    step=step,
                    sequence=event_seq,
                    event_type=(
                        EventType.TOOL_RESPONSE
                        if output.get("status") == "success"
                        else EventType.TOOL_ERROR
                    ),
                    payload=output,
                    payload_size_bytes=len(str(output)),
                    tool_name=tool_name,
                )
                history.append({"role": "assistant", "content": call})
                history.append({"role": "user", "content": {"tool_result": output}})

            if response.get("final_text") is not None:
                # Agent produced the brief.
                run.final_output = {"text": response["final_text"], "raw": response}
                run.termination_reason = TerminationReason.AGENT_DECIDED_DONE
                run.status = AgentRunStatus.COMPLETED
                break

    except LoopAbort as abort:  # allow: suppress-exception
        # LoopAbort is the loop's own termination sentinel — catching it is
        # how we drive the structured finish path, not error swallowing.
        run.termination_reason = abort.reason
        run.status = (
            AgentRunStatus.KILLED
            if abort.reason == TerminationReason.KILLED_BY_HUMAN
            else AgentRunStatus.BUDGET_EXHAUSTED
            if abort.reason
            in {
                TerminationReason.BUDGET_STEPS,
                TerminationReason.BUDGET_COST,
                TerminationReason.BUDGET_DURATION,
            }
            else AgentRunStatus.FAILED
        )
        run.error_summary = abort.detail or ""
    except Exception as exc:
        # Mark the run as failed so the trajectory viewer reflects it, then
        # let the exception propagate so Celery (and any wrapping
        # observability) sees the crash. The finally block persists the row.
        run.status = AgentRunStatus.FAILED
        run.termination_reason = TerminationReason.ERROR
        run.error_summary = repr(exc)
        raise
    finally:
        run.ended_at = timezone.now()
        run.duration_used_s = int(time.monotonic() - start_time)
        run.duration_ms = run.duration_used_s * 1000
        run.steps_used = budget.steps
        run.cost_used_usd = Decimal(str(round(budget.cost_usd, 4)))
        run.save()
        cache.clear()
