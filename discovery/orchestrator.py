"""Transactional discovery lifecycle, turn ownership and dialogue handoffs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from agents.prompts import load_prompt
from discovery.mail import send_message
from discovery.models import (
    Discovery,
    DiscoveryDispatch,
    DiscoveryTask,
    DiscoveryTurn,
    JudgmentRevision,
    ProposalRevision,
    ValidationRevision,
)
from discovery.reporting import render_report
from discovery.schemas import Contract, DiscoveryConfig, Judgment, Proposal, Validation
from discovery.termination import (
    SubmissionError,
    evidence_gaps,
    has_independent_research,
    require_agreement,
    require_judge_approval,
    require_judge_feedback,
    validate_references,
)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class DiscoveryError(ValueError):
    """Invalid discovery request."""


class DiscoveryConflict(RuntimeError):
    """An operation key was reused or a transition conflicts with current state."""


class LostLease(RuntimeError):
    """This worker no longer owns the dialogue turn."""


def content_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def start_discovery(
    objective: str,
    config: dict[str, Any] | DiscoveryConfig | None = None,
    constraints: str = "",
    idempotency_key: str | None = None,
    enqueue: bool = True,
) -> Discovery:
    if not isinstance(objective, str) or not objective.strip():
        raise DiscoveryError("A nonempty objective is required")
    if len(objective) > 20000 or len(constraints) > 20000:
        raise DiscoveryError("Objective and constraints must each be at most 20,000 characters")
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
        raise DiscoveryError("Idempotency key must be 1–200 characters")
    defaults = {
        "runtime": getattr(settings, "DISCOVERY_RUNTIME", "claude_code"),
        "finder_provider": getattr(settings, "DISCOVERY_FINDER_PROVIDER", "openrouter"),
        "validator_provider": getattr(settings, "DISCOVERY_VALIDATOR_PROVIDER", "openrouter"),
        "finder_model": getattr(settings, "DISCOVERY_FINDER_MODEL", None),
        "validator_model": getattr(settings, "DISCOVERY_VALIDATOR_MODEL", None),
        "judge_provider": getattr(settings, "DISCOVERY_JUDGE_PROVIDER", "ollama"),
        "judge_model": getattr(settings, "DISCOVERY_JUDGE_MODEL", "kimi-k3:cloud"),
        "connectors": getattr(settings, "DISCOVERY_CONNECTORS", None),
    }
    values = {key: value for key, value in defaults.items() if value is not None}
    if isinstance(config, DiscoveryConfig):
        values.update(config.model_dump())
    elif config:
        values.update(config)
    if "judge_enabled" not in values and values.get("schema_version") not in {"1.0", "1.1"}:
        values["judge_enabled"] = getattr(settings, "DISCOVERY_JUDGE_ENABLED", True)
    try:
        cfg = DiscoveryConfig.model_validate(values)
    except ValidationError as exc:
        raise DiscoveryError(str(exc)) from exc
    payload = {
        "objective": objective.strip(),
        "constraints": constraints,
        "config": cfg.model_dump(mode="json"),
    }
    request_hash = content_hash(payload)
    if idempotency_key:
        existing = Discovery.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.request_hash != request_hash:
                raise DiscoveryConflict("Idempotency key already used for a different discovery")
            return existing
    # Registry is imported here to avoid coupling database models to connector startup.
    from discovery.connectors.registry import get_connector  # noqa: PLC0415

    connector_snapshot = []
    for connector_id in cfg.connectors:
        connector = get_connector(connector_id)
        connector.check_configuration()
        description = connector.describe()
        connector_snapshot.append(
            description.model_dump(mode="json")
            if hasattr(description, "model_dump")
            else description
        )
    from discovery.runtime import check_configuration  # noqa: PLC0415  # runtime preflight

    runtime_snapshot = check_configuration(cfg)
    prompts = {}
    for role in ("finder", "validator", "judge") if cfg.judge_enabled else ("finder", "validator"):
        prompt = load_prompt(role, "system")
        prompts[role] = {"content": prompt.content, "hash": prompt.hash, "path": prompt.path}
    with transaction.atomic():
        discovery, created = (
            Discovery.objects.get_or_create(
                idempotency_key=idempotency_key,
                defaults={
                    "objective": objective.strip(),
                    "constraints": constraints,
                    "config_snapshot": cfg.model_dump(mode="json"),
                    "prompt_snapshot": prompts,
                    "connector_snapshot": connector_snapshot,
                    "runtime_snapshot": runtime_snapshot,
                    "request_hash": request_hash,
                },
            )
            if idempotency_key
            else (
                Discovery.objects.create(
                    objective=objective.strip(),
                    constraints=constraints,
                    config_snapshot=cfg.model_dump(mode="json"),
                    prompt_snapshot=prompts,
                    connector_snapshot=connector_snapshot,
                    runtime_snapshot=runtime_snapshot,
                    request_hash=request_hash,
                ),
                True,
            )
        )
        if not created:
            if discovery.request_hash != request_hash:
                raise DiscoveryConflict("Idempotency key already used for a different discovery")
            return discovery
        task = DiscoveryTask.objects.create(discovery=discovery, role="finder", turn_number=1)
        send_message(
            discovery_id=discovery.pk,
            sender="human",
            recipient="finder",
            kind="request",
            subject="Find an opportunity",
            body=objective.strip(),
            operation_key="initial-request",
            task=task,
        )
        DiscoveryDispatch.objects.create(task=task)
        if enqueue:
            transaction.on_commit(_kick_dispatch)
    return discovery


def _kick_dispatch() -> None:
    from discovery.tasks import dispatch_pending  # noqa: PLC0415  # Celery import cycle

    dispatch_pending.delay()


def claim_task(task_id: UUID | str) -> DiscoveryTask | None:
    with transaction.atomic():
        base = DiscoveryTask.objects.get(pk=task_id)
        discovery = Discovery.objects.select_for_update().get(pk=base.discovery_id)
        task = DiscoveryTask.objects.select_for_update().get(pk=task_id)
        if discovery.status in TERMINAL_STATUSES or task.state in {"done", "cancelled", "failed"}:
            return None
        now = timezone.now()
        if task.state == "working" and task.lease_expires_at and task.lease_expires_at > now:
            return None
        if (
            task.operations.filter(
                state__in=["started", "unknown"],
            )
            .exclude(kind="research", reserved_usd=0)
            .exists()
        ):
            _finish_locked(
                discovery,
                "worker_lost",
                "inconclusive",
                "failed",
                "A prior billed operation has an unresolved outcome; reconcile before retrying",
            )
            return None
        # Known-unbilled reads can be repeated after a crash; billed calls
        # remain blocked above until their outcome is reconciled.
        task.operations.filter(
            kind="research",
            state__in=["started", "unknown"],
            reserved_usd=0,
        ).update(state="planned", error_summary="")
        task.state = "working"
        task.attempts += 1
        task.lease_token = uuid.uuid4()
        task.lease_expires_at = now + timedelta(
            seconds=getattr(settings, "DISCOVERY_LEASE_SECONDS", 180)
        )
        task.save(update_fields=["state", "attempts", "lease_token", "lease_expires_at"])
        if discovery.started_at is None:
            discovery.started_at = now
        discovery.status = "running"
        discovery.save(update_fields=["status", "started_at", "updated_at"])
        DiscoveryTurn.objects.get_or_create(
            task=task,
            defaults={"discovery": discovery, "number": task.turn_number, "role": task.role},
        )
    return task


def check_lease(task_id: UUID | str, lease_token: UUID | str) -> DiscoveryTask:
    task = DiscoveryTask.objects.select_related("discovery").get(pk=task_id)
    if (
        task.state != "working"
        or str(task.lease_token) != str(lease_token)
        or task.lease_expires_at is None
        or task.lease_expires_at <= timezone.now()
        or task.discovery.status in TERMINAL_STATUSES
    ):
        raise LostLease("Task lease expired, was cancelled, or belongs to another worker")
    return task


def renew_task(task_id: UUID | str, lease_token: UUID | str) -> None:
    task = check_lease(task_id, lease_token)
    updated = DiscoveryTask.objects.filter(
        pk=task.pk, lease_token=task.lease_token, state="working"
    ).update(
        lease_expires_at=timezone.now()
        + timedelta(seconds=getattr(settings, "DISCOVERY_LEASE_SECONDS", 180)),
    )
    if not updated:
        raise LostLease("Task lease changed during renewal")


def release_task(task_id: UUID | str, lease_token: UUID | str) -> None:
    task = check_lease(task_id, lease_token)
    updated = DiscoveryTask.objects.filter(
        pk=task.pk, lease_token=task.lease_token, state="working"
    ).update(
        state="open",
        lease_token=None,
        lease_expires_at=None,
    )
    if not updated:
        raise LostLease("Task lease changed during release")


def commit_turn(
    task_id: UUID | str,
    lease_token: UUID | str,
    submission: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
    model: str = "",
    prompt_hash: str = "",
    enqueue: bool = True,
    validate_only: bool = False,
) -> Discovery:
    base = DiscoveryTask.objects.get(pk=task_id)
    schemas: dict[str, type[Contract]] = {
        "finder": Proposal,
        "validator": Validation,
        "judge": Judgment,
    }
    if base.role not in schemas:
        raise DiscoveryError("Unknown discovery role")
    schema = schemas[base.role]
    parsed = schema.model_validate(submission)
    normalized = parsed.model_dump(mode="json")
    digest = content_hash(normalized)
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=base.discovery_id)
        task = DiscoveryTask.objects.select_for_update().get(pk=task_id)
        turn = DiscoveryTurn.objects.select_for_update().get(task=task)
        if task.state == "done":
            if turn.submission_hash != digest:
                raise DiscoveryConflict("Completed turn submitted with different content")
            return discovery
        check_lease(task_id, lease_token)
        cfg = DiscoveryConfig.model_validate(discovery.config_snapshot)
        if (
            discovery.started_at
            and (timezone.now() - discovery.started_at).total_seconds() >= cfg.max_duration_s
        ):
            return _finish_locked(discovery, "duration_limit", "inconclusive", "completed", "")
        if discovery.cost_used_usd >= cfg.max_cost_usd:
            return _finish_locked(discovery, "cost_limit", "inconclusive", "completed", "")
        validate_references(discovery, normalized)
        if task.turn_number != discovery.turns_completed + 1:
            raise DiscoveryConflict("Turn is out of sequence")
        latest_proposal = discovery.proposals.order_by("-turn__number").first()
        latest_validation = discovery.validations.order_by("-turn__number").first()
        if task.role == "finder":
            proposal = Proposal.model_validate(normalized)
            if proposal.parent_revision_id:
                if not latest_proposal or str(latest_proposal.pk) != proposal.parent_revision_id:
                    raise SubmissionError("Parent must be the latest proposal in this discovery")
            elif latest_proposal:
                raise SubmissionError("A revised proposal must reference the previous revision")
            require_judge_feedback(discovery, normalized)
        elif task.role == "validator":
            validation = Validation.model_validate(normalized)
            if not latest_proposal or validation.reviewed_proposal_id != str(latest_proposal.pk):
                raise SubmissionError("Validator must review the latest Finder revision")
            if validation.reviewed_proposal_hash != latest_proposal.content_hash:
                raise SubmissionError("Reviewed proposal hash does not match")
            if not has_independent_research(task, require_search=validation.verdict == "accept"):
                raise SubmissionError(
                    "Validator needs independent research this turn: a new successful search, "
                    "or for a challenge a fetched source not used by Finder. "
                    "Acceptance always requires a new successful search."
                )
            require_agreement(discovery, task, latest_proposal, validation)
        else:
            judgment = Judgment.model_validate(normalized)
            if not cfg.judge_enabled:
                raise SubmissionError("Judge is not enabled in this discovery")
            if not latest_proposal or judgment.reviewed_proposal_id != str(latest_proposal.pk):
                raise SubmissionError("Judge must review the latest Finder revision")
            if judgment.reviewed_proposal_hash != latest_proposal.content_hash:
                raise SubmissionError("Judge reviewed proposal hash does not match")
            if (
                latest_validation is None
                or latest_validation.proposal_id != latest_proposal.pk
                or latest_validation.verdict != "accept"
                or judgment.reviewed_validation_id != str(latest_validation.pk)
                or judgment.reviewed_validation_hash != latest_validation.content_hash
            ):
                raise SubmissionError(
                    "Judge must review the exact latest accepted Validator revision"
                )
            require_judge_approval(discovery, latest_proposal, judgment)
        if validate_only:
            return discovery
        now = timezone.now()
        turn.submission = normalized
        turn.submission_hash = digest
        turn.model = model
        turn.prompt_hash = prompt_hash
        turn.completed_at = now
        if input_snapshot is not None:
            turn.input_snapshot = input_snapshot
        turn.save()
        revision: ProposalRevision | ValidationRevision | JudgmentRevision
        if task.role == "finder":
            revision = ProposalRevision.objects.create(
                discovery=discovery,
                turn=turn,
                parent=latest_proposal,
                content=normalized,
                content_hash=digest,
            )
        elif task.role == "validator":
            if latest_proposal is None:
                raise SubmissionError("Validation requires an existing proposal")
            revision = ValidationRevision.objects.create(
                discovery=discovery,
                turn=turn,
                proposal=latest_proposal,
                verdict=normalized["verdict"],
                content=normalized,
                content_hash=digest,
            )
        else:
            if latest_proposal is None or latest_validation is None:
                raise SubmissionError("Judgment requires an accepted proposal and validation")
            revision = JudgmentRevision.objects.create(
                discovery=discovery,
                turn=turn,
                proposal=latest_proposal,
                validation=latest_validation,
                verdict=normalized["verdict"],
                content=normalized,
                content_hash=digest,
            )
        task.state = "done"
        task.completed_at = now
        task.lease_expires_at = None
        task.save(update_fields=["state", "completed_at", "lease_expires_at"])
        discovery.turns_completed += 1
        discovery.save(update_fields=["turns_completed", "updated_at"])
        prior_message = task.messages.order_by("-created_at").first()
        recipient = (
            "judge"
            if task.role == "validator" and normalized["verdict"] == "accept" and cfg.judge_enabled
            else "validator"
            if task.role == "finder"
            else "finder"
        )
        message = send_message(
            discovery_id=discovery.pk,
            sender=task.role,
            recipient=recipient,
            kind="reply",
            subject=f"Turn {task.turn_number}: {task.role.title()} brief",
            body=normalized["summary"],
            operation_key=f"turn-{task.turn_number}-reply",
            task=task,
            reply_to=prior_message,
            artifact_id=revision.pk,
        )
        if task.role == "judge" and normalized["verdict"] == "approve":
            return _finish_locked(discovery, "judge_approval", "converged", "completed", "")
        if task.role == "validator" and normalized["verdict"] == "accept" and not cfg.judge_enabled:
            return _finish_locked(discovery, "agreement", "converged", "completed", "")
        if discovery.turns_completed >= cfg.max_turns:
            outcome = (
                "not_converged"
                if (
                    task.role == "judge"
                    or (task.role == "validator" and normalized["verdict"] != "accept")
                )
                and latest_proposal is not None
                and not evidence_gaps(discovery, latest_proposal)
                else "inconclusive"
            )
            return _finish_locked(discovery, "turn_limit", outcome, "completed", "")
        next_task = DiscoveryTask.objects.create(
            discovery=discovery,
            role=recipient,
            turn_number=task.turn_number + 1,
        )
        send_message(
            discovery_id=discovery.pk,
            sender=task.role,
            recipient=recipient,
            kind="request",
            subject="Judge this agreed proposal"
            if recipient == "judge"
            else "Validate this proposal"
            if recipient == "validator"
            else "Find a new opportunity after Judge rejection"
            if task.role == "judge" and normalized["verdict"] == "reject"
            else "Revise or find a new opportunity",
            body=(
                "Judge rejected this direction. Abandon it and find a new opportunity. "
                if task.role == "judge" and normalized["verdict"] == "reject"
                else ""
            )
            + normalized["summary"],
            operation_key=f"turn-{task.turn_number}-handoff",
            task=next_task,
            reply_to=message,
            artifact_id=revision.pk,
        )
        DiscoveryDispatch.objects.create(task=next_task)
        if enqueue:
            transaction.on_commit(_kick_dispatch)
    return discovery


def _finish_locked(
    discovery: Discovery,
    stop_reason: str,
    outcome: str,
    status: str,
    error_summary: str,
) -> Discovery:
    if discovery.status in TERMINAL_STATUSES:
        return discovery
    discovery.status = status
    discovery.outcome = outcome
    discovery.stop_reason = stop_reason
    discovery.error_summary = error_summary
    discovery.ended_at = timezone.now()
    discovery.tasks.filter(state__in=["open", "working"]).update(
        state="cancelled" if status == "cancelled" else "failed",
        lease_token=None,
        lease_expires_at=None,
    )
    discovery.save(
        update_fields=[
            "status",
            "outcome",
            "stop_reason",
            "error_summary",
            "ended_at",
            "updated_at",
        ]
    )
    send_message(
        discovery_id=discovery.pk,
        sender="system",
        recipient="human",
        kind="note",
        subject=f"Discovery {outcome.replace('_', ' ')}",
        body=f"Stopped: {stop_reason}.",
        operation_key=f"terminal-note:{discovery.turns_completed}:{stop_reason}:{outcome}",
    )
    markdown, report = render_report(discovery)
    discovery.report_markdown = markdown
    discovery.report_json = report
    discovery.save(update_fields=["report_markdown", "report_json", "updated_at"])
    return discovery


def finish_discovery(
    discovery_id: UUID | str,
    stop_reason: str,
    outcome: str = "inconclusive",
    status: str = "completed",
    error_summary: str = "",
) -> Discovery:
    if status not in TERMINAL_STATUSES:
        raise DiscoveryError("Invalid terminal status")
    if outcome not in {"converged", "not_converged", "inconclusive"}:
        raise DiscoveryError("Invalid discovery outcome")
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=discovery_id)
        return _finish_locked(discovery, stop_reason, outcome, status, error_summary)


def cancel_discovery(discovery_id: UUID | str) -> Discovery:
    return finish_discovery(discovery_id, "human_cancelled", status="cancelled")


def regenerate_report(discovery_id: UUID | str) -> Discovery:
    with transaction.atomic():
        discovery = Discovery.objects.select_for_update().get(pk=discovery_id)
        if discovery.status not in TERMINAL_STATUSES:
            raise DiscoveryConflict("Report is not ready while a discovery is running")
        markdown, report = render_report(discovery)
        discovery.report_markdown, discovery.report_json = markdown, report
        discovery.save(update_fields=["report_markdown", "report_json", "updated_at"])
    return discovery
