"""Ideation orchestrator.

Parallels ``agents.orchestrator`` but for the ideation agent, which takes
an investigation (not a cluster) as primary input. Under the hood this
delegates to ``agents.orchestrator.start_run`` — the AgentRun cluster FK
points at ``investigation.cluster`` (still the conceptual ground truth),
and the investigation brief is carried in ``config_snapshot["ideation_input"]``
via the ``extra_snapshot`` seam.

Module boundary (per AGENTS.md §10): this is the only place that creates
``Ideation`` rows. Tools do not write to the DB; the loop persists output
back onto the existing ``Ideation`` row it was given via the snapshot.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from agents.models import AgentRun
from agents.orchestrator import BudgetConfig, start_run
from ideation.models import Ideation
from investigations.models import Investigation


def _snapshot_investigation(inv: Investigation) -> dict[str, Any]:
    """Freeze the investigation state at run start.

    The loop reads ``brief`` and ``cluster_snapshot`` from here; later
    mutations to the investigation (e.g. brief replacement on re-run)
    do not affect an in-flight ideation.
    """
    return {
        "id": str(inv.id),
        "brief": inv.brief,
        "brief_schema_version": inv.brief_schema_version,
        "cluster_snapshot": inv.cluster_snapshot,
        "cluster_id": str(inv.cluster_id),
    }


def start_ideation(
    investigation_id: UUID | str,
    guidance: str = "",
    trigger: str = "manual",
    budget: BudgetConfig | None = None,
) -> tuple[UUID, UUID]:
    """Create an Ideation row, snapshot the investigation, enqueue the agent run.

    Returns ``(ideation_id, run_id)``.
    """
    inv = Investigation.objects.select_related("cluster").get(pk=investigation_id)

    with transaction.atomic():
        ideation = Ideation.objects.create(
            investigation=inv,
            guidance=guidance,
        )

    extra_snapshot = {
        "ideation_input": {
            "ideation_id": str(ideation.id),
            "investigation": _snapshot_investigation(inv),
            "guidance": guidance,
        }
    }

    run_id = start_run(
        cluster_id=inv.cluster_id,
        agent_name="ideation",
        trigger=trigger,
        budget=budget,
        extra_snapshot=extra_snapshot,
    )

    Ideation.objects.filter(pk=ideation.id).update(primary_run=AgentRun.objects.get(pk=run_id))

    return ideation.id, run_id
