"""Celery tasks for the agent loop."""

from __future__ import annotations

from celery import shared_task

from .loop import run_loop


@shared_task
def run_agent_loop(run_id: str) -> None:
    """Worker entry point — orchestrator enqueues this after creating the run."""
    run_loop(run_id)
