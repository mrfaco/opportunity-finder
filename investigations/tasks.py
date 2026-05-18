"""Celery tasks for investigations."""

from __future__ import annotations

from celery import shared_task


@shared_task
def mark_stale_investigations() -> dict:
    """Periodic sweep — mark investigations whose underlying cluster or prompts have changed.

    TODO(v1-followup): implement. Sketch:
      * Compare ``cluster_snapshot.size`` against the current cluster size.
        If drift exceeds a threshold, mark stale with ``stale_reason=cluster_changed``.
      * For each investigation, look up the run's prompt hashes; compare with
        current prompt hashes. If different, ``stale_reason=prompt_changed``.
      * Age-based staleness for items beyond a configurable window.
    """
    raise NotImplementedError("TODO(v1-followup): implement staleness sweep")
