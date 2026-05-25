"""Cluster lifecycle operations.

Single source of truth for the state transitions on
``ClusterMergeProposal``. Both admin actions and the HTTP API call into
here so the business logic — moving items between clusters, recomputing
the survivor's centroid, marking the absorbed cluster ``MERGED_INTO``,
flipping the proposal status — lives in exactly one place.

Module boundary (AGENTS.md §10): only this module mutates merge-
proposal status fields and the related cluster status / item-cluster
FKs. The judge that *advises* on proposals lives in
``clusters.judges``; the candidate detection lives in
``clusters.clustering``.
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from clusters import clustering
from clusters.models import (
    ClusterMergeProposal,
    ClusterStatus,
    ProposalStatus,
)


class MergeProposalNotFound(LookupError):
    """Merge proposal with the requested id does not exist."""


class MergeProposalNotInState(RuntimeError):
    """Proposal is in the wrong status for the requested transition.

    Carries both the actual and expected status so the API can render a
    useful 409 body — and so callers reading logs can see which row was
    out of state.
    """

    def __init__(self, *, proposal_id: UUID, current_status: str, expected_status: str):
        self.proposal_id = proposal_id
        self.current_status = current_status
        self.expected_status = expected_status
        super().__init__(
            f"Merge proposal {proposal_id} is in status {current_status!r}; "
            f"expected {expected_status!r}."
        )


def apply_merge_proposal(
    *, proposal_id: UUID | str, user: AbstractBaseUser
) -> ClusterMergeProposal:
    """Apply a pending merge: items move from cluster_b → cluster_a.

    Returns the proposal with its post-apply state (status APPLIED,
    reviewed_by/at populated). Raises ``MergeProposalNotInState`` if the
    row is not pending_review.

    Concurrent calls are serialized via ``select_for_update`` on the
    proposal row + the precondition recheck inside the transaction —
    two simultaneous applies can't both win.
    """
    with transaction.atomic():
        proposal = _lock_proposal(proposal_id)
        if proposal.status != ProposalStatus.PENDING_REVIEW:
            raise MergeProposalNotInState(
                proposal_id=proposal.id,
                current_status=proposal.status,
                expected_status=ProposalStatus.PENDING_REVIEW,
            )
        survivor = proposal.cluster_a
        absorbed = proposal.cluster_b
        now = timezone.now()

        absorbed.items.update(cluster=survivor, assigned_at=now)
        clustering.recompute_centroid(survivor)

        survivor.merge_history = [
            *survivor.merge_history,
            {
                "absorbed_cluster_id": str(absorbed.id),
                "applied_at": now.isoformat(),
                "centroid_similarity": proposal.centroid_similarity,
                "proposal_id": str(proposal.id),
            },
        ]
        survivor.save(update_fields=["merge_history", "updated_at"])

        absorbed.status = ClusterStatus.MERGED_INTO
        absorbed.merged_into_cluster = survivor
        absorbed.save(update_fields=["status", "merged_into_cluster", "updated_at"])

        ClusterMergeProposal.objects.filter(pk=proposal.pk).update(
            status=ProposalStatus.APPLIED,
            reviewed_by=user,
            reviewed_at=now,
        )
        proposal.refresh_from_db()
    return proposal


def reject_merge_proposal(
    *,
    proposal_id: UUID | str,
    user: AbstractBaseUser,
    review_notes: str = "",
) -> ClusterMergeProposal:
    """Reject a pending merge proposal. No cluster mutations."""
    with transaction.atomic():
        proposal = _lock_proposal(proposal_id)
        if proposal.status != ProposalStatus.PENDING_REVIEW:
            raise MergeProposalNotInState(
                proposal_id=proposal.id,
                current_status=proposal.status,
                expected_status=ProposalStatus.PENDING_REVIEW,
            )
        update_fields = {
            "status": ProposalStatus.REJECTED,
            "reviewed_by": user,
            "reviewed_at": timezone.now(),
        }
        if review_notes:
            update_fields["review_notes"] = review_notes
        ClusterMergeProposal.objects.filter(pk=proposal.pk).update(**update_fields)
        proposal.refresh_from_db()
    return proposal


def _lock_proposal(proposal_id: UUID | str) -> ClusterMergeProposal:
    """Select-for-update fetch; raise MergeProposalNotFound if missing.

    Always called inside ``transaction.atomic()``; the row lock holds for
    the rest of the transaction and is released on commit/rollback.
    """
    try:
        return ClusterMergeProposal.objects.select_for_update().get(pk=proposal_id)
    except ClusterMergeProposal.DoesNotExist as exc:
        raise MergeProposalNotFound(f"Merge proposal {proposal_id} does not exist.") from exc
