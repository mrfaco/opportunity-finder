"""Research dialogue and independent Judge, with durable role handoffs."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from pydantic import ValidationError

from discovery import runtime
from discovery.errors import BatchPending, DiscoveryStop, OutcomeUnknown
from discovery.mail import inbox
from discovery.models import DiscoveryTask, ResearchOperation
from discovery.orchestrator import (
    LostLease,
    claim_task,
    commit_turn,
    finish_discovery,
    release_task,
    renew_task,
)
from discovery.research import (
    ResearchInput,
    begin_operation,
    check_limits,
    complete_operation,
    lease_token,
    perform_research,
)
from discovery.schemas import Contract, DiscoveryConfig, Judgment, Proposal, Validation
from discovery.termination import SubmissionError, unresolved_objections

logger = logging.getLogger(__name__)


def tools_for_role(role: str) -> list[dict[str, Any]]:
    roles: dict[str, tuple[type[Contract], str]] = {
        "finder": (Proposal, "submit_proposal"),
        "validator": (Validation, "submit_validation"),
        "judge": (Judgment, "submit_judgment"),
    }
    schema, submit = roles[role]
    return [
        {
            "type": "function",
            "function": {
                "name": "research",
                "description": (
                    "Search/fetch an enabled connector. Cite returned evidence_id values. "
                    "Search snippets are leads; fetch decisive pages."
                ),
                "parameters": ResearchInput.model_json_schema(),
            },
        },
        {
            "type": "function",
            "function": {
                "name": submit,
                "description": "Finish this role's turn and deliver the artifact to the workflow.",
                "parameters": schema.model_json_schema(),
            },
        },
    ]


def _initial_context(task: DiscoveryTask) -> dict[str, Any]:
    discovery = task.discovery
    latest_proposal = discovery.proposals.order_by("-turn__number").first()
    latest_validation = discovery.validations.order_by("-turn__number").first()
    accepted_validation = (
        discovery.validations.filter(proposal=latest_proposal, verdict="accept")
        .order_by("-turn__number")
        .first()
        if latest_proposal
        else None
    )
    judgments = list(discovery.judgments.select_related("proposal").order_by("turn__number"))
    # Summaries keep prior turns bounded. Current artifacts and all unresolved
    # objections are preserved intact, with evidence available by source URL.
    return {
        "schema_version": "1.1",
        "role": task.role,
        "objective": discovery.objective,
        "constraints": discovery.constraints,
        "turn_number": task.turn_number,
        "config": discovery.config_snapshot,
        "connectors": discovery.connector_snapshot,
        "proposal": {
            "id": str(latest_proposal.pk),
            "hash": latest_proposal.content_hash,
            "content": latest_proposal.content,
        }
        if latest_proposal
        else None,
        "validation": latest_validation.content if latest_validation else None,
        "accepted_validation": {
            "id": str(accepted_validation.pk),
            "hash": accepted_validation.content_hash,
            "content": accepted_validation.content,
        }
        if accepted_validation
        else None,
        "judgment": {
            "id": str(judgments[-1].pk),
            "hash": judgments[-1].content_hash,
            "content": judgments[-1].content,
        }
        if judgments
        else None,
        "judgment_history": [
            {
                "proposal_id": str(judgment.proposal_id),
                "proposal_title": judgment.proposal.content.get("title", ""),
                "verdict": judgment.verdict,
                "content": judgment.content,
            }
            for judgment in judgments
        ],
        "rejected_opportunities": [
            {
                "proposal_id": str(judgment.proposal_id),
                "proposal": judgment.proposal.content,
                "judgment": judgment.content,
            }
            for judgment in judgments
            if judgment.verdict == "reject"
        ],
        "unresolved_objections": unresolved_objections(discovery),
        "history": [
            {"turn": turn.number, "role": turn.role, "summary": turn.submission.get("summary", "")}
            for turn in discovery.turns.filter(completed_at__isnull=False)
        ],
        "evidence": [
            {
                "evidence_id": str(e.pk),
                "url": e.url,
                "title": e.title,
                "kind": e.kind,
                "source_type": e.source_type,
                "origin": e.origin,
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "excerpt": e.content[:1500],
                "content_hash": e.content_hash,
            }
            for e in discovery.evidence.all()
        ],
        "mail": [
            {
                "id": str(message.pk),
                "sender": message.sender,
                "subject": message.subject,
                "body": message.body,
                "artifact_id": str(message.artifact_id) if message.artifact_id else None,
                "reply_to": str(message.reply_to_id) if message.reply_to_id else None,
            }
            for message in inbox(discovery.pk, task.role)
        ],
    }


def _save_context(task: DiscoveryTask, context: dict[str, Any]) -> None:
    renew_task(task.pk, lease_token(task))
    updated = DiscoveryTask.objects.filter(
        pk=task.pk, lease_token=task.lease_token, state="working"
    ).update(context=context)
    if not updated:
        raise LostLease("Turn ownership changed while persisting model history")
    task.context = context


def _execute(task: DiscoveryTask) -> None:
    config = DiscoveryConfig.model_validate(task.discovery.config_snapshot)
    if config.runtime == "claude_code":
        from discovery.claude_runtime import run_turn  # noqa: PLC0415  # runtime selection

        run_turn(task, _initial_context(task), tools_for_role(task.role))
        return
    model = getattr(config, f"{task.role}_model")
    prompt = task.discovery.prompt_snapshot[task.role]
    context = task.context
    if not context:
        inputs = _initial_context(task)
        task.turn.input_snapshot = inputs
        task.turn.model = model
        task.turn.prompt_hash = prompt["hash"]
        task.turn.save(update_fields=["input_snapshot", "model", "prompt_hash"])
        context = {
            "history": [
                {"role": "system", "content": prompt["content"]},
                {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
            ],
            "model_index": 0,
            "pending_model_key": None,
        }
        _save_context(task, context)
    tools = tools_for_role(task.role)
    while True:
        renew_task(task.pk, lease_token(task))
        check_limits(task)
        history = context["history"]
        key = context.get("pending_model_key")
        if key:
            operation = ResearchOperation.objects.get(discovery=task.discovery, operation_key=key)
        else:
            body = runtime.request_body(model, history, tools, config.max_output_tokens)
            key = f"{task.pk}:model:{context['model_index']}"
            # Operation and history cursor are durable before the paid POST.
            with transaction.atomic():
                operation = begin_operation(
                    task,
                    key,
                    "model",
                    {"body": body},
                    runtime.reservation(body, task.discovery.runtime_snapshot, model),
                )
                context["pending_model_key"] = key
                _save_context(task, context)
        result = runtime.advance(operation, task.discovery.runtime_snapshot["provider"])
        complete_operation(task, operation, result)
        renew_task(task.pk, lease_token(task))
        check_limits(task)
        message = result["message"]
        history.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                **({"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {}),
                **(
                    {"reasoning_details": message["reasoning_details"]}
                    if message.get("reasoning_details")
                    else {}
                ),
            }
        )
        submissions = [
            call
            for call in message.get("tool_calls", [])
            if call["function"]["name"].startswith("submit_")
        ]
        if len(submissions) > 1:
            raise SubmissionError("Only one submission is allowed in a model response")
        # Run research before terminal submissions even if the provider returns
        # multiple tool calls with submission listed first.
        calls = sorted(
            message.get("tool_calls", []),
            key=lambda call: call["function"]["name"].startswith("submit_"),
        )
        for call in calls:
            check_limits(task)
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"]["arguments"])
                if name == "research":
                    output = perform_research(task, f"{key}:tool:{call['id']}", arguments)
                elif (
                    name
                    == {
                        "finder": "submit_proposal",
                        "validator": "submit_validation",
                        "judge": "submit_judgment",
                    }[task.role]
                ):
                    commit_turn(
                        task.pk,
                        lease_token(task),
                        arguments,
                        model=model,
                        prompt_hash=prompt["hash"],
                    )
                    return
                else:
                    raise ValueError("Tool is not available to this role")  # noqa: TRY301
            except (ValueError, ValidationError) as exc:  # allow: suppress-exception
                # This is the agent's explicit schema-correction protocol. The
                # failed validation is visible in its persisted next model input.
                output = {"schema_version": "1.0", "status": "validation_failed", "error": str(exc)}
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(output, ensure_ascii=False),
                }
            )
        if not message.get("tool_calls"):
            history.append(
                {
                    "role": "user",
                    "content": (
                        "Continue research or finish using your submission tool; "
                        "prose alone is not a handoff."
                    ),
                }
            )
        context["model_index"] += 1
        context["pending_model_key"] = None
        _save_context(task, context)


def run_task(task_id: str) -> None:
    task = claim_task(task_id)
    if task is None:
        return
    try:
        _execute(task)
    except BatchPending:  # allow: suppress-exception
        # A pending batch is persisted work, not a failed or completed turn.
        release_task(task.pk, lease_token(task))
        from discovery.tasks import run_discovery_turn  # noqa: PLC0415  # task/runtime cycle

        run_discovery_turn.apply_async(
            args=[str(task.pk)], countdown=settings.DISCOVERY_BATCH_POLL_SECONDS
        )
    except LostLease:  # allow: suppress-exception
        # Cancellation/reclamation invalidated this worker. The current owner
        # alone may change discovery state.
        return
    except DiscoveryStop as exc:  # allow: suppress-exception
        finish_discovery(
            task.discovery_id,
            exc.reason,
            status="failed" if exc.reason == "invalid_submission" else "completed",
            error_summary=str(exc),
        )
    except OutcomeUnknown as exc:
        task.operations.filter(state__in=["started", "pending"]).update(state="unknown")
        finish_discovery(
            task.discovery_id, "provider_error", status="failed", error_summary=str(exc)
        )
        raise
    except Exception as exc:  # noqa: BLE001  # persist a failure report then propagate
        logger.exception("Discovery turn failed: %s", task.pk)
        task.operations.filter(state="started").update(state="unknown")
        finish_discovery(
            task.discovery_id,
            "invalid_submission" if isinstance(exc, SubmissionError) else "provider_error",
            status="failed",
            error_summary=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        raise
