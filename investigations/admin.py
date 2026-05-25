from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import render
from django.utils import timezone

from investigations.models import Investigation, InvestigationStatus, StaleReason

_LATEST_LIMIT = 50


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    """Triage queue for generated briefs.

    The list view defaults to "awaiting review" ordered newest-first. The
    change view uses a custom template (``review.html``) rendering the brief
    on the left, cluster context on the right, decision form below.
    """

    change_form_template = "admin/investigations/investigation/review.html"

    list_display = (
        "id",
        "cluster",
        "status",
        "build_status",
        "in_eval_set",
        "created_at",
        "finalized_at",
    )
    list_filter = ("status", "build_status", "in_eval_set", "stale_reason")
    search_fields = ("id", "cluster__title", "brief__headline")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "finalized_at",
        "primary_run",
        "supporting_run_ids",
        "cluster_snapshot",
        "stale_marked_at",
        "decided_at",
        "build_status_updated_at",
    )

    actions = ["promote", "reject", "mark_stale", "promote_to_eval_set"]

    def get_changelist_instance(self, request):
        # Default to "awaiting review" if no explicit filter.
        if not request.GET:
            request.GET = request.GET.copy()
            request.GET["status__exact"] = InvestigationStatus.AWAITING_REVIEW
        return super().get_changelist_instance(request)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra = extra_context or {}
        if object_id:
            obj = self.get_object(request, object_id)
            if obj is not None:
                extra["investigation"] = obj
                extra["cluster"] = obj.cluster
                extra["primary_run"] = obj.primary_run
                extra["brief"] = obj.brief
        return super().changeform_view(request, object_id, form_url, extra)

    @admin.action(description="Promote selected investigations")
    def promote(self, request, queryset):
        n = queryset.filter(status=InvestigationStatus.AWAITING_REVIEW).update(
            status=InvestigationStatus.PROMOTED,
            decided_by_user=request.user,
            decided_at=timezone.now(),
            finalized_at=timezone.now(),
        )
        messages.success(request, f"Promoted {n} investigation(s).")

    @admin.action(description="Reject selected investigations")
    def reject(self, request, queryset):
        n = queryset.filter(status=InvestigationStatus.AWAITING_REVIEW).update(
            status=InvestigationStatus.REJECTED,
            decided_by_user=request.user,
            decided_at=timezone.now(),
            finalized_at=timezone.now(),
        )
        messages.success(request, f"Rejected {n} investigation(s).")

    @admin.action(description="Mark selected as stale")
    def mark_stale(self, request, queryset):
        n = queryset.update(
            status=InvestigationStatus.STALE,
            stale_reason=StaleReason.MANUAL,
            stale_marked_at=timezone.now(),
        )
        messages.success(request, f"Marked {n} as stale.")

    @admin.action(description="Promote to eval set")
    def promote_to_eval_set(self, request, queryset):
        n = queryset.update(in_eval_set=True)
        messages.success(request, f"Added {n} to the eval set.")

    # ------------------------------------------------------------------
    # Latest investigations dashboard — newest-first list of generated
    # briefs with headline/confidence/cost pulled out for scanning.
    # The standard changelist defaults to "awaiting review" only; this
    # view includes all statuses (filter via ?status=...) and surfaces
    # the brief's interesting bits without one-by-one drill-down.
    # ------------------------------------------------------------------
    def latest_view(self, request):
        status_filter = request.GET.get("status")
        qs = Investigation.objects.select_related("cluster", "primary_run").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)

        rows = []
        for inv in qs[:_LATEST_LIMIT]:
            brief = inv.brief or {}
            rows.append(
                {
                    "investigation": inv,
                    "headline": brief.get("headline") or "(no headline)",
                    "problem": (brief.get("problem_statement") or "")[:160],
                    "confidence": brief.get("confidence"),
                    "target_user": (brief.get("target_user") or "")[:120],
                    "cluster": inv.cluster,
                    "run": inv.primary_run,
                    "cost_usd": inv.primary_run.cost_used_usd,
                    "steps": inv.primary_run.steps_used,
                    "created_at": inv.created_at,
                    "status": inv.status,
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Latest investigations",
            "rows": rows,
            "status_choices": InvestigationStatus.choices,
            "current_status": status_filter,
            "limit": _LATEST_LIMIT,
        }
        return render(request, "admin/investigations/latest.html", context)
