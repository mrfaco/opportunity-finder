from __future__ import annotations

from django.contrib import admin
from django.shortcuts import render
from django.urls import path

from .models import (
    FilterClassification,
    FilterEvalClassification,
    FilterEvalRun,
    FilterEvalSet,
    IngestionCheckpoint,
)


@admin.register(IngestionCheckpoint)
class IngestionCheckpointAdmin(admin.ModelAdmin):
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


@admin.register(FilterClassification)
class FilterClassificationAdmin(admin.ModelAdmin):
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
class FilterEvalSetAdmin(admin.ModelAdmin):
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
class FilterEvalRunAdmin(admin.ModelAdmin):
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
class FilterEvalClassificationAdmin(admin.ModelAdmin):
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
