from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from ingestion import tasks
from ingestion.models import (
    FilterClassification,
    FilterEvalClassification,
    FilterEvalRun,
    FilterEvalSet,
    IngestionCheckpoint,
)


@admin.register(IngestionCheckpoint)
class IngestionCheckpointAdmin(UnfoldModelAdmin):
    list_display = (
        "source",
        "last_item_posted_at",
        "last_run_at",
        "items_seen",
        "opportunities_found",
    )
    readonly_fields = (
        "source",
        "last_item_posted_at",
        "last_run_at",
        "items_seen",
        "opportunities_found",
    )

    # ------------------------------------------------------------------
    # Ingestion operations dashboard — per-source checkpoint state plus
    # one-click triggers for incremental ingest and backfill, enqueued via
    # Celery so the request returns immediately while the worker grinds.
    # ------------------------------------------------------------------
    def operations_view(self, request):
        # Deferred to avoid a top-level ingestion→clusters import (the modules
        # already have a tangled reverse FK relationship) and so django-celery-
        # results — a runtime-only dep — doesn't have to be importable at module
        # load time for tests that don't exercise this view.
        from django_celery_results.models import TaskResult  # noqa: PLC0415

        from clusters.models import ClusterItem  # noqa: PLC0415

        checkpoints = {cp.source: cp for cp in IngestionCheckpoint.objects.all()}
        rows = []
        for source in sorted(tasks.ADAPTERS):
            cp = checkpoints.get(source)
            rows.append(
                {
                    "source": source,
                    "last_item_posted_at": cp.last_item_posted_at if cp else None,
                    "last_run_at": cp.last_run_at if cp else None,
                    "items_seen": cp.items_seen if cp else 0,
                    "opportunities_found": cp.opportunities_found if cp else 0,
                }
            )
        latest_items = ClusterItem.objects.select_related("cluster").order_by("-assigned_at")[:25]
        # Show the 25 most recent ingestion task runs (in-progress + finished).
        # Filter to ``ingestion.tasks.*`` so the page isn't polluted by agent
        # loops, refinements, etc. — those have their own dashboards.
        task_runs = TaskResult.objects.filter(
            task_name__startswith="ingestion.tasks.",
        ).order_by("-date_created")[:25]
        context = {
            **self.admin_site.each_context(request),
            "title": "Ingestion operations",
            "rows": rows,
            "latest_items": latest_items,
            "task_runs": task_runs,
        }
        return render(request, "admin/ingestion/operations.html", context)

    def trigger_ingest_view(self, request, source):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if source not in tasks.ADAPTERS:
            messages.error(request, f"Unknown source {source!r}.")
            return HttpResponseRedirect("/admin/ingestion/operations/")
        async_result = tasks.ingest_source.delay(source)
        messages.success(
            request,
            f"Incremental ingest queued for {source}. Celery task id: {async_result.id}.",
        )
        return HttpResponseRedirect("/admin/ingestion/operations/")

    def trigger_backfill_view(self, request, source):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if source not in tasks.ADAPTERS:
            messages.error(request, f"Unknown source {source!r}.")
            return HttpResponseRedirect("/admin/ingestion/operations/")
        days_str = (request.POST.get("days") or "").strip()
        if not days_str.isdigit() or int(days_str) <= 0:
            messages.error(request, "Days must be a positive integer.")
            return HttpResponseRedirect("/admin/ingestion/operations/")
        days = int(days_str)
        async_result = tasks.backfill_source_task.delay(source, days)
        messages.success(
            request,
            f"Backfill queued: {source} for {days}d. Celery task id: {async_result.id}.",
        )
        return HttpResponseRedirect("/admin/ingestion/operations/")


@admin.register(FilterClassification)
class FilterClassificationAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "model",
        "prompt_hash",
        "is_opportunity",
        "confidence",
        "verdict_band",
        "discarded",
        "classified_at",
    )
    list_filter = (
        "verdict_band",
        "discard_reason",
        "discarded",
        "is_opportunity",
        "model",
    )
    search_fields = ("prompt_hash", "reason", "item__title")
    readonly_fields = ("id", "classified_at")


@admin.register(FilterEvalSet)
class FilterEvalSetAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "human_label",
        "difficulty_tier",
        "source",
        "sourced_from",
        "labeled_by",
        "labeled_at",
    )
    list_filter = ("human_label", "difficulty_tier", "sourced_from", "source")
    search_fields = ("content", "reasoning_note")
    readonly_fields = ("id", "labeled_at")

    def get_urls(self):
        custom = [
            path(
                "filter-labeling/",
                self.admin_site.admin_view(self.labeling_view),
                name="ingestion-filter-labeling",
            ),
            path(
                "filter-eval-history/",
                self.admin_site.admin_view(self.eval_history_view),
                name="ingestion-filter-eval-history",
            ),
        ]
        return custom + super().get_urls()

    def labeling_view(self, request):
        """Keyboard-driven labeling UI.

        TODO(v1-followup): full implementation requires the prompt v1.0 +
        seed eval set session. For now we render a placeholder describing
        the intended UX.
        """
        context = {
            **self.admin_site.each_context(request),
            "title": "Filter eval — labeling",
            "todo": (
                "TODO(v1-followup): implement bulk labeling with keyboard "
                "shortcuts (Y/N/A/?), model-verdict toggle (off by default), "
                "note field, progress counter, advance-on-submit."
            ),
            "pending_count": FilterClassification.objects.filter(human_label__isnull=True).count(),
            "labeled_count": FilterEvalSet.objects.count(),
        }
        return render(request, "admin/ingestion/filter_labeling.html", context)

    def eval_history_view(self, request):
        runs = FilterEvalRun.objects.order_by("-run_at")[:25]
        context = {
            **self.admin_site.each_context(request),
            "title": "Filter eval — history",
            "runs": runs,
            "latest": runs.first() if runs else None,
        }
        return render(request, "admin/ingestion/filter_eval_history.html", context)


@admin.register(FilterEvalRun)
class FilterEvalRunAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "run_at",
        "model",
        "prompt_hash",
        "eval_set_size",
        "precision",
        "recall",
        "f1",
        "total_cost_usd",
    )
    list_filter = ("model", "prompt_hash")
    readonly_fields = (
        "id",
        "run_at",
        "prompt_hash",
        "prompt_content",
        "model",
        "eval_set_size",
        "precision",
        "recall",
        "f1",
        "metrics_by_tier",
        "eval_set_snapshot",
        "total_cost_usd",
        "total_duration_s",
    )


@admin.register(FilterEvalClassification)
class FilterEvalClassificationAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "eval_run",
        "eval_item",
        "model_verdict",
        "model_confidence",
        "agrees_with_label",
    )
    list_filter = ("model_verdict", "agrees_with_label")
    readonly_fields = ("id",)
