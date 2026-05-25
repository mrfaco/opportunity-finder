from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import markdown as _md
from django.contrib import admin
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import path
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from agents import cost as cost_mod
from agents import prompts as prompt_loader
from agents.models import AgentEvent, AgentRun, AgentStep


@admin.register(AgentRun)
class AgentRunAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "agent_name",
        "cluster",
        "status",
        "trigger",
        "steps_used",
        "cost_used_usd",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "agent_name", "trigger", "termination_reason")
    search_fields = ("id", "cluster__title")
    readonly_fields = (
        "id",
        "started_at",
        "ended_at",
        "duration_ms",
        "cluster",
        "cluster_snapshot",
        "config_snapshot",
        "prompt_hashes",
        "final_output",
        "models_used",
        "termination_reason",
        "error_summary",
    )

    def get_urls(self):
        custom = [
            path(
                "prompts/",
                self.admin_site.admin_view(self.prompt_inspector_view),
                name="agents-prompt-inspector",
            ),
            path(
                "cost-dashboard/",
                self.admin_site.admin_view(self.cost_dashboard_view),
                name="agents-cost-dashboard",
            ),
            path(
                "run/<uuid:run_id>/trajectory/",
                self.admin_site.admin_view(self.trajectory_view),
                name="agents-trajectory",
            ),
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Prompt inspector — read-only.
    # ------------------------------------------------------------------
    def prompt_inspector_view(self, request):
        prompts = prompt_loader.list_all_prompts()
        # Count runs per prompt hash so the inspector shows usage at a glance.
        hash_counts: dict[str, int] = {}
        for p in prompts:
            hash_counts[p.hash] = AgentRun.objects.filter(
                prompt_hashes__contains={p.kind: p.hash}
            ).count()
        rendered = [
            {
                "prompt": p,
                "html": mark_safe(_md.markdown(p.content)),
                "runs_used": hash_counts.get(p.hash, 0),
            }
            for p in prompts
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": "Prompts (read-only)",
            "prompts": rendered,
            "note": (
                "Prompts are managed in git. To edit, modify the file in "
                "prompts/, commit, and redeploy. History is captured per run "
                "in AgentRun.config_snapshot."
            ),
        }
        return render(request, "admin/agents/prompt_inspector.html", context)

    # ------------------------------------------------------------------
    # Cost dashboard.
    # ------------------------------------------------------------------
    def cost_dashboard_view(self, request):
        today = date.today()
        today_summary = cost_mod.daily_cost_summary(today)

        last_7 = []
        for i in range(7):
            d = today - timedelta(days=i)
            last_7.append(cost_mod.daily_cost_summary(d))

        per_model = (
            AgentStep.objects.values("model")
            .annotate(steps=Count("id"), cost=Sum("cost_usd"))
            .order_by("-cost")
        )

        top_runs = AgentRun.objects.filter(started_at__gte=today - timedelta(days=7)).order_by(
            "-cost_used_usd"
        )[:5]

        context = {
            **self.admin_site.each_context(request),
            "title": "Cost dashboard",
            "today": today_summary,
            "last_7": last_7,
            "per_model": per_model,
            "top_runs": top_runs,
            "totals_7d": sum((Decimal(str(d["total_cost_usd"])) for d in last_7), Decimal("0")),
        }
        return render(request, "admin/agents/cost_dashboard.html", context)

    # ------------------------------------------------------------------
    # Trajectory viewer — skeleton.
    # ------------------------------------------------------------------
    def trajectory_view(self, request, run_id):
        run = get_object_or_404(AgentRun, pk=run_id)
        steps = run.steps.order_by("step_number")
        events = run.events.order_by("recorded_at")
        context = {
            **self.admin_site.each_context(request),
            "title": f"Trajectory · run {run.id}",
            "run": run,
            "steps": steps,
            "events": events,
            "note": (
                "TODO(v1-followup): polish the trajectory viewer — collapsible "
                "payloads, side-by-side model+tool view, syntax highlighting."
            ),
        }
        return render(request, "admin/agents/trajectory.html", context)


@admin.register(AgentStep)
class AgentStepAdmin(UnfoldModelAdmin):
    list_display = (
        "run",
        "step_number",
        "step_type",
        "tool_name",
        "tool_status",
        "cost_usd",
        "model",
        "started_at",
    )
    list_filter = ("step_type", "tool_status", "tool_name", "model")
    readonly_fields = tuple(f.name for f in AgentStep._meta.get_fields() if not f.is_relation)


@admin.register(AgentEvent)
class AgentEventAdmin(UnfoldModelAdmin):
    list_display = ("run", "step", "sequence", "event_type", "tool_name", "recorded_at")
    list_filter = ("event_type", "tool_name")
    readonly_fields = tuple(f.name for f in AgentEvent._meta.get_fields() if not f.is_relation)
