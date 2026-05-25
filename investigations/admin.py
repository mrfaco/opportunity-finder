from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from ideation.orchestrator import start_ideation
from investigations.models import Investigation, InvestigationStatus, StaleReason

_LATEST_LIMIT = 50


@admin.register(Investigation)
class InvestigationAdmin(UnfoldModelAdmin):
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

    @admin.action(description="Promote selected investigations (and ideate)")
    def promote(self, request, queryset):
        n = self._promote_ids(
            request.user,
            list(
                queryset.filter(status=InvestigationStatus.AWAITING_REVIEW).values_list(
                    "id", flat=True
                )
            ),
        )
        messages.success(
            request,
            f"Promoted {n} investigation(s) and enqueued ideation runs.",
        )

    @staticmethod
    def _promote_ids(user, investigation_ids):
        """Flip awaiting-review rows to promoted and enqueue an ideation each.

        Shared between the queryset action and the per-row latest-view button.
        """
        n = Investigation.objects.filter(pk__in=investigation_ids).update(
            status=InvestigationStatus.PROMOTED,
            decided_by_user=user,
            decided_at=timezone.now(),
            finalized_at=timezone.now(),
        )
        for inv_id in investigation_ids:
            start_ideation(investigation_id=inv_id, guidance="", trigger="promote")
        return n

    def promote_from_latest_view(self, request, investigation_id):
        """POST-only per-row promote button on the latest-investigations page.

        Redirects back to the latest view so the operator stays in the
        scanning context after promoting.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        inv = Investigation.objects.filter(
            pk=investigation_id, status=InvestigationStatus.AWAITING_REVIEW
        ).first()
        if inv is None:
            messages.warning(
                request,
                f"Investigation {investigation_id} is not in awaiting_review; nothing to do.",
            )
        else:
            self._promote_ids(request.user, [inv.id])
            messages.success(
                request,
                f"Promoted investigation {inv.id} and enqueued ideation.",
            )
        return HttpResponseRedirect("/admin/investigations/latest/")

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
            "awaiting_review_status": InvestigationStatus.AWAITING_REVIEW.value,
        }
        return render(request, "admin/investigations/latest.html", context)
