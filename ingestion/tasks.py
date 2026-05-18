"""Celery tasks for ingestion + classification."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def ingest_source(source: str) -> dict:
    """Run one adapter; classify and cluster each new item.

    TODO(v1-followup): wire up the adapter → classifier → clustering pipeline.
    Sketch:
      1. Instantiate the right ``SourceAdapter`` subclass.
      2. For each new item from ``fetch_new_items``:
         a. Compute embedding.
         b. Call ``ingestion.filter.classify_item``.
         c. If verdict is opportunity, persist a ``ClusterItem``, run
            ``clusters.clustering.assign_item_to_cluster``, write a
            ``FilterClassification`` row.
         d. Otherwise, write a discarded ``FilterClassification`` only.
    """
    raise NotImplementedError(
        f"TODO(v1-followup): implement ingest_source pipeline for source={source!r}"
    )


@shared_task
def run_filter_eval(prompt_hash: str | None = None) -> dict:
    """Run the classifier across the eval set; record metrics.

    TODO(v1-followup): implement. Loads the current prompt if hash is None,
    otherwise the historical content recovered from FilterEvalRun.
    """
    raise NotImplementedError("TODO(v1-followup): implement filter eval run")
