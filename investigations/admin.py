from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from core.pdf import render_pdf
from investigations.models import Investigation, InvestigationStatus
from investigations.orchestrator import (
    InvestigationNotInState,
    mark_stale_one,
    promote_investigation,
    reject_investigation,
)

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
        ids = list(
            queryset.filter(status=InvestigationStatus.AWAITING_REVIEW).values_list("id", flat=True)
        )
        n = 0
        for inv_id in ids:
            # Each id is a fresh transaction. A concurrent flip is rare here
            # (admin actions are operator-driven) but the orchestrator handles
            # it correctly — we surface the conflict as a warning.
            try:
                promote_investigation(investigation_id=inv_id, user=request.user)
                n += 1
            except InvestigationNotInState as exc:  # allow: suppress-exception
                # Per-row conflict in a batch action: warn the operator and
                # keep going so one stale row doesn't abort the whole batch.
                messages.warning(request, str(exc))
        messages.success(
            request,
            f"Promoted {n} investigation(s) and enqueued ideation runs.",
        )

    def promote_from_latest_view(self, request, investigation_id):
        """POST-only per-row promote button on the latest-investigations page.

        Redirects back to the latest view so the operator stays in the
        scanning context after promoting.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        try:
            promote_investigation(investigation_id=investigation_id, user=request.user)
        except InvestigationNotInState as exc:  # allow: suppress-exception
            # Per-row admin UX: a stale row gets surfaced as a warning so the
            # operator can keep scanning rather than seeing a 500.
            messages.warning(request, str(exc))
        else:
            messages.success(
                request,
                f"Promoted investigation {investigation_id} and enqueued ideation.",
            )
        return HttpResponseRedirect("/admin/investigations/latest/")

    @admin.action(description="Reject selected investigations")
    def reject(self, request, queryset):
        ids = list(
            queryset.filter(status=InvestigationStatus.AWAITING_REVIEW).values_list("id", flat=True)
        )
        n = 0
        for inv_id in ids:
            try:
                reject_investigation(investigation_id=inv_id, user=request.user)
                n += 1
            except InvestigationNotInState as exc:  # allow: suppress-exception
                # Same rationale as promote(): per-row conflicts surface as
                # warnings, batch keeps going.
                messages.warning(request, str(exc))
        messages.success(request, f"Rejected {n} investigation(s).")

    @admin.action(description="Mark selected as stale")
    def mark_stale(self, request, queryset):
        n = 0
        for inv_id in queryset.values_list("id", flat=True):
            mark_stale_one(investigation_id=inv_id)
            n += 1
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

    # ------------------------------------------------------------------
    # PDF download — used by both the admin review page button and the
    # API endpoint at /api/v1/investigations/<id>/pdf/. The renderer
    # itself is in core/pdf.py (WeasyPrint behind one function).
    # ------------------------------------------------------------------
    def download_pdf_view(self, request, investigation_id):
        inv = get_object_or_404(
            Investigation.objects.select_related("cluster"), pk=investigation_id
        )
        pdf_bytes = render_pdf(
            "pdf/investigation.html",
            {
                "investigation": inv,
                "brief": inv.brief or {},
                "cluster": inv.cluster,
            },
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        # ``inline`` so a click in admin previews in the browser; the
        # download attribute on the link makes browsers save instead.
        response["Content-Disposition"] = f'inline; filename="investigation-{inv.id}.pdf"'
        return response
