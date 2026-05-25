"""Ideation review surface.

Triage queue for generated ideations: list with status / guidance / created
date, change view rendering the three concept cards, accept / reject
actions, and a re-ideate URL that takes an investigation id and an
optional guidance string.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone

from ideation.models import Ideation, IdeationStatus
from ideation.orchestrator import start_ideation
from investigations.models import Investigation


@admin.register(Ideation)
class IdeationAdmin(admin.ModelAdmin):
    change_form_template = "admin/ideation/ideation/review.html"

    list_display = (
        "id",
        "investigation",
        "status",
        "guidance_preview",
        "created_at",
        "primary_run",
    )
    list_filter = ("status",)
    search_fields = ("id", "investigation__id", "guidance", "output__concepts__name")
    readonly_fields = (
        "id",
        "investigation",
        "guidance",
        "output",
        "output_schema_version",
        "primary_run",
        "created_at",
        "updated_at",
        "decided_at",
        "decided_by_user",
    )

    actions = ["accept", "reject"]

    @admin.display(description="Guidance")
    def guidance_preview(self, obj):
        if not obj.guidance:
            return "—"
        return obj.guidance[:80] + ("…" if len(obj.guidance) > 80 else "")

    def get_changelist_instance(self, request):
        # Default to "awaiting review" if no explicit filter.
        if not request.GET:
            request.GET = request.GET.copy()
            request.GET["status__exact"] = IdeationStatus.AWAITING_REVIEW
        return super().get_changelist_instance(request)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra = extra_context or {}
        if object_id:
            obj = self.get_object(request, object_id)
            if obj is not None:
                extra["ideation"] = obj
                extra["investigation"] = obj.investigation
                extra["output"] = obj.output or {}
                extra["concepts"] = (obj.output or {}).get("concepts", [])
                extra["ideation_notes"] = (obj.output or {}).get("ideation_notes", "")
                extra["primary_run"] = obj.primary_run
                extra["re_ideate_url"] = reverse(
                    "admin:ideation_ideation_re_ideate",
                    args=[str(obj.investigation_id)],
                )
        return super().changeform_view(request, object_id, form_url, extra)

    @admin.action(description="Accept selected ideations")
    def accept(self, request, queryset):
        n = queryset.filter(status=IdeationStatus.AWAITING_REVIEW).update(
            status=IdeationStatus.ACCEPTED,
            decided_by_user=request.user,
            decided_at=timezone.now(),
        )
        messages.success(request, f"Accepted {n} ideation(s).")

    @admin.action(description="Reject selected ideations")
    def reject(self, request, queryset):
        n = queryset.filter(status=IdeationStatus.AWAITING_REVIEW).update(
            status=IdeationStatus.REJECTED,
            decided_by_user=request.user,
            decided_at=timezone.now(),
        )
        messages.success(request, f"Rejected {n} ideation(s).")

    # ------------------------------------------------------------------
    # Re-ideate URL: GET shows a guidance textarea, POST kicks off a new
    # ideation run for the given investigation.
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "re_ideate/<uuid:investigation_id>/",
                self.admin_site.admin_view(self.re_ideate_view),
                name="ideation_ideation_re_ideate",
            ),
        ]
        return custom + urls

    def re_ideate_view(self, request, investigation_id):
        inv = Investigation.objects.get(pk=investigation_id)
        if request.method == "POST":
            guidance = request.POST.get("guidance", "").strip()
            ideation_id, _run_id = start_ideation(
                investigation_id=inv.id,
                guidance=guidance,
                trigger="manual",
            )
            messages.success(
                request,
                "Re-ideation enqueued. The ideation row is in draft and will "
                "flip to awaiting_review when the agent completes.",
            )
            return HttpResponseRedirect(
                reverse("admin:ideation_ideation_change", args=[str(ideation_id)])
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Re-ideate investigation",
            "investigation": inv,
            "brief": inv.brief or {},
        }
        return render(request, "admin/ideation/ideation/re_ideate.html", context)
