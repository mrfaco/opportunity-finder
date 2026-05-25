"""Investigation lifecycle operations.

Single source of truth for the four state transitions a human (or the API)
can drive on an ``Investigation``:

* ``start_investigation`` — enqueue a new investigation run for a cluster.
* ``promote_investigation`` — awaiting_review → promoted, enqueues an ideation.
* ``reject_investigation`` — awaiting_review → rejected.
* ``mark_stale_one`` — any non-terminal status → stale.

Both the admin actions and the HTTP API call into here so the business
logic — required preconditions, status flips, follow-on enqueues — lives
in exactly one place. Concurrent calls are serialized via
``select_for_update`` on the row; the precondition check inside the
transaction is what guarantees we never double-enqueue ideations.

Module boundary (AGENTS.md §10): only this module mutates ``Investigation``
status fields. The admin's queryset-update shortcuts call helpers here.
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from agents.orchestrator import BudgetConfig, start_run
from ideation.orchestrator import start_ideation
from investigations.models import Investigation, InvestigationStatus, StaleReason


class InvestigationNotFound(LookupError):
    """Investigation with the requested id does not exist."""


class InvestigationNotInState(RuntimeError):
    """Investigation is in the wrong status for the requested transition.

    Carries both the actual and expected status so the API can render a
    useful 409 body — and so callers reading logs can see which row was
    out of state.
    """

    def __init__(self, *, investigation_id: UUID, current_status: str, expected_status: str):
        self.investigation_id = investigation_id
        self.current_status = current_status
        self.expected_status = expected_status
        super().__init__(
            f"Investigation {investigation_id} is in status {current_status!r}; "
            f"expected {expected_status!r}."
        )


def start_investigation(
    *,
    cluster_id: UUID | str,
    trigger: str = "api",
    budget: BudgetConfig | None = None,
) -> UUID:
    """Snapshot the cluster, persist an AgentRun, enqueue the loop.

    Returns the new ``AgentRun.id``. An ``Investigation`` row materializes
    later, when the loop finishes and produces a brief — so the synchronous
    response gives the caller the run id, not an investigation id.
    """
    return start_run(
        cluster_id=cluster_id, agent_name="investigation", trigger=trigger, budget=budget
    )


def promote_investigation(
    *,
    investigation_id: UUID | str,
    user: AbstractBaseUser,
    guidance: str = "",
) -> tuple[UUID, UUID]:
    """Flip awaiting_review → promoted and enqueue an ideation.

    Returns ``(ideation_id, ideation_run_id)``. Raises
    ``InvestigationNotInState`` if the row is not awaiting_review.

    The state flip + ideation enqueue happen inside one transaction with
    ``select_for_update`` so two concurrent callers can't both win.
    """
    with transaction.atomic():
        inv = _lock_investigation(investigation_id)
        if inv.status != InvestigationStatus.AWAITING_REVIEW:
            raise InvestigationNotInState(
                investigation_id=inv.id,
                current_status=inv.status,
                expected_status=InvestigationStatus.AWAITING_REVIEW,
            )
        now = timezone.now()
        Investigation.objects.filter(pk=inv.pk).update(
            status=InvestigationStatus.PROMOTED,
            decided_by_user=user,
            decided_at=now,
            finalized_at=now,
        )
        # Enqueue inside the transaction is fine here: ``start_ideation``
        # writes its own Ideation row (and the Celery enqueue) using the
        # default autocommit transaction nesting. If anything below this
        # raises, the status update rolls back too — exactly the behavior
        # we want.
        ideation_id, ideation_run_id = start_ideation(
            investigation_id=inv.id, guidance=guidance, trigger="promote"
        )
    return ideation_id, ideation_run_id


def reject_investigation(
    *,
    investigation_id: UUID | str,
    user: AbstractBaseUser,
    reason: str = "",
) -> None:
    """Flip awaiting_review → rejected. Reason stored in ``human_decision``."""
    with transaction.atomic():
        inv = _lock_investigation(investigation_id)
        if inv.status != InvestigationStatus.AWAITING_REVIEW:
            raise InvestigationNotInState(
                investigation_id=inv.id,
                current_status=inv.status,
                expected_status=InvestigationStatus.AWAITING_REVIEW,
            )
        now = timezone.now()
        Investigation.objects.filter(pk=inv.pk).update(
            status=InvestigationStatus.REJECTED,
            decided_by_user=user,
            decided_at=now,
            finalized_at=now,
            human_decision={"decision": "reject", "reason": reason},
        )


def mark_stale_one(
    *,
    investigation_id: UUID | str,
    stale_reason: str = StaleReason.MANUAL,
) -> None:
    """Mark a single investigation stale.

    Unlike promote/reject this is idempotent on already-stale rows (we just
    bump ``stale_marked_at``) and doesn't require a particular starting
    status — staleness can apply to draft, awaiting_review, or promoted
    rows when e.g. the cluster shape changes underneath them.
    """
    valid_reasons = {choice for choice, _ in StaleReason.choices}
    if stale_reason not in valid_reasons:
        raise ValueError(
            f"Invalid stale_reason {stale_reason!r}; expected one of {sorted(valid_reasons)}."
        )
    with transaction.atomic():
        inv = _lock_investigation(investigation_id)
        Investigation.objects.filter(pk=inv.pk).update(
            status=InvestigationStatus.STALE,
            stale_reason=stale_reason,
            stale_marked_at=timezone.now(),
        )


def _lock_investigation(investigation_id: UUID | str) -> Investigation:
    """Select-for-update fetch; raise InvestigationNotFound if missing.

    Always called inside ``transaction.atomic()``; the row lock holds for
    the rest of the transaction and is released on commit or rollback.
    """
    try:
        return Investigation.objects.select_for_update().get(pk=investigation_id)
    except Investigation.DoesNotExist as exc:
        raise InvestigationNotFound(f"Investigation {investigation_id} does not exist.") from exc
