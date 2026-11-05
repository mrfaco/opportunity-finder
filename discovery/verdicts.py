"""Append-only human verdicts; the only writer of ``DiscoveryVerdict`` rows."""

from __future__ import annotations

from typing import Literal

from django.contrib.auth.models import User
from django.db import transaction

from discovery.models import Discovery, DiscoveryVerdict
from discovery.orchestrator import TERMINAL_STATUSES, regenerate_report
from discovery.schemas import VerdictInput


class VerdictNotAllowed(RuntimeError):
    """A verdict was submitted for a discovery that has not reached a terminal state."""


def record_verdict(
    discovery: Discovery,
    verdict: str,
    notes: str,
    user: User | None,
    via: Literal["admin", "api"],
) -> DiscoveryVerdict:
    data = VerdictInput.model_validate({"verdict": verdict, "notes": notes})
    with transaction.atomic():
        locked = Discovery.objects.select_for_update().get(pk=discovery.pk)
        if locked.status not in TERMINAL_STATUSES:
            raise VerdictNotAllowed(
                f"A verdict can be recorded only on a completed, failed or cancelled "
                f"discovery; this one is {locked.status}."
            )
        # Keep what the reviewer saw: the final proposal and its latest Judge verdict.
        proposal = locked.proposals.order_by("-turn__number").first()
        judgment = (
            locked.judgments.filter(proposal=proposal).order_by("-turn__number").first()
            if proposal
            else None
        )
        row = DiscoveryVerdict.objects.create(
            discovery=locked,
            proposal=proposal,
            judgment=judgment,
            verdict=data.verdict,
            notes=data.notes,
            recorded_by=user,
            recorded_via=via,
        )
        regenerate_report(locked.pk)
    return row
