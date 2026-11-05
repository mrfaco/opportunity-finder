"""Operator interface for starting and inspecting opportunity discoveries."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import cast
from urllib.parse import urlsplit

import bleach
import markdown
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin

from discovery.api import report_response
from discovery.connectors.base import ConnectorConfigurationError
from discovery.connectors.registry import describe_connectors
from discovery.eval.benchmark import load_benchmark
from discovery.models import Discovery
from discovery.orchestrator import (
    TERMINAL_STATUSES,
    DiscoveryConflict,
    cancel_discovery,
    start_discovery,
)
from discovery.schemas import DEFAULT_MODEL
from discovery.verdicts import VerdictNotAllowed, record_verdict

_FORM_ERRORS = (ValueError, DiscoveryConflict, ConnectorConfigurationError)


class DiscoveryStartForm(forms.Form):
    objective = forms.CharField(
        max_length=12000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Look for opportunities in travel"}),
    )
    constraints = forms.CharField(
        required=False,
        max_length=12000,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional geography, capital, skills, customer or operating constraints.",
    )
    max_turns = forms.IntegerField(initial=100, min_value=2, max_value=200)
    max_cost_usd = forms.DecimalField(
        initial="10.00",
        min_value=Decimal("0.0001"),
        decimal_places=4,
        help_text="Ollama Cloud uses estimated spend checked between agent wakes. "
        "A wake can exceed this amount; this is not a provider billing cap.",
    )
    max_duration_s = forms.IntegerField(initial=1800, min_value=1)
    max_model_calls_per_turn = forms.IntegerField(initial=8, min_value=1, max_value=50)
    max_research_calls_per_turn = forms.IntegerField(initial=8, min_value=1, max_value=50)
    max_output_tokens = forms.IntegerField(
        initial=24000,
        min_value=256,
        max_value=32000,
        help_text="Maximum response size for each model call, shared by all enabled roles.",
    )
    finder_provider = forms.ChoiceField(
        choices=[
            ("openrouter", "OpenRouter"),
            ("anthropic", "Anthropic / Claude Code login"),
            ("ollama", "Ollama"),
        ],
        initial="openrouter",
        help_text="Every provider uses Claude Code. Configure credentials on the host.",
    )
    finder_model = forms.CharField(
        initial=DEFAULT_MODEL,
        max_length=160,
        help_text="OpenRouter: provider/model; Anthropic: sonnet/native ID; Ollama: exact tag.",
    )
    validator_provider = forms.ChoiceField(
        choices=[
            ("openrouter", "OpenRouter"),
            ("anthropic", "Anthropic / Claude Code login"),
            ("ollama", "Ollama"),
        ],
        initial="openrouter",
    )
    validator_model = forms.CharField(
        initial=DEFAULT_MODEL,
        max_length=160,
        help_text="Prefer a different model family from Finder. Independent sessions of "
        "the same model can share blind spots. Select the provider separately above.",
    )
    judge_enabled = forms.BooleanField(
        required=False,
        initial=True,
        label="Require Judge approval",
        help_text="Finder–Validator agreement goes to Judge. A no-go sends the agents "
        "to find a new opportunity within the same turn, time and spending limits.",
    )
    judge_provider = forms.ChoiceField(
        choices=[
            ("openrouter", "OpenRouter"),
            ("anthropic", "Anthropic / Claude Code login"),
            ("ollama", "Ollama"),
        ],
        initial="ollama",
    )
    judge_model = forms.CharField(
        initial="kimi-k3:cloud",
        max_length=160,
        help_text="Use a stronger independent model for commercial judgment. "
        "The default is Ollama Kimi K3; its usage consumes the same run budget.",
    )
    connectors = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple)
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cast(forms.MultipleChoiceField, self.fields["connectors"]).choices = [
            (item["id"], item.get("name", item["id"])) for item in describe_connectors()
        ]
        self.fields["connectors"].initial = settings.DISCOVERY_CONNECTORS
        self.fields["finder_provider"].initial = settings.DISCOVERY_FINDER_PROVIDER
        self.fields["validator_provider"].initial = settings.DISCOVERY_VALIDATOR_PROVIDER
        self.fields["finder_model"].initial = settings.DISCOVERY_FINDER_MODEL
        self.fields["validator_model"].initial = settings.DISCOVERY_VALIDATOR_MODEL
        self.fields["judge_enabled"].initial = getattr(settings, "DISCOVERY_JUDGE_ENABLED", True)
        self.fields["judge_provider"].initial = getattr(
            settings, "DISCOVERY_JUDGE_PROVIDER", "ollama"
        )
        self.fields["judge_model"].initial = getattr(
            settings, "DISCOVERY_JUDGE_MODEL", "kimi-k3:cloud"
        )
        self.fields["idempotency_key"].initial = uuid.uuid4()

    def discovery_config(self):
        keys = (
            "max_turns",
            "max_cost_usd",
            "max_duration_s",
            "max_model_calls_per_turn",
            "max_research_calls_per_turn",
            "max_output_tokens",
            "finder_provider",
            "finder_model",
            "validator_provider",
            "validator_model",
            "judge_enabled",
            "judge_provider",
            "judge_model",
            "connectors",
        )
        return {
            "schema_version": "1.2",
            "runtime": "claude_code",
            **{key: self.cleaned_data[key] for key in keys},
        }


class VerdictForm(forms.Form):
    verdict = forms.ChoiceField(
        choices=[
            ("good", "Good — worth testing next; I would act on it"),
            ("weak", "Weak — plausible, but evidence or economics do not justify acting"),
            ("wrong", "Wrong — not viable or a material factual error (say which)"),
            ("untested", "Untested — cannot judge without information outside the report"),
        ],
        widget=forms.RadioSelect,
    )
    notes = forms.CharField(
        required=False,
        max_length=12000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required for 'wrong'. Judge the opportunity, not the report's formatting.",
    )

    def clean(self):
        super().clean()
        if self.cleaned_data.get("verdict") == "wrong" and not self.cleaned_data.get("notes"):
            raise forms.ValidationError("A 'wrong' verdict must say what is wrong.")
        return self.cleaned_data


def _url(value: str) -> str:
    return value if urlsplit(value).scheme in {"http", "https"} else ""


@admin.register(Discovery)
class DiscoveryAdmin(ModelAdmin):
    list_display = (
        "objective",
        "status",
        "outcome",
        "turns_completed",
        "cost_used_usd",
        "created_at",
    )
    list_filter = ("status", "outcome", "stop_reason", "benchmark_tag", "benchmark_objective_id")
    search_fields = ("objective", "constraints", "id")
    ordering = ("-created_at",)
    actions = None

    def get_urls(self):
        custom = [
            path("start/", self.admin_site.admin_view(self.start_view), name="discovery-start"),
            path(
                "<uuid:discovery_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="discovery-review",
            ),
            path(
                "<uuid:discovery_id>/report/",
                self.admin_site.admin_view(self.report_view),
                name="discovery-report",
            ),
            path(
                "<uuid:discovery_id>/verdict/",
                self.admin_site.admin_view(self.verdict_view),
                name="discovery-verdict",
            ),
            path(
                "<uuid:discovery_id>/cancel/",
                self.admin_site.admin_view(self.cancel_view),
                name="discovery-cancel",
            ),
        ]
        return custom + super().get_urls()

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        return HttpResponseRedirect(reverse("admin:discovery-start"))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return HttpResponseRedirect(reverse("admin:discovery-review", args=[object_id]))

    def start_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        if request.method not in {"GET", "POST"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        form = DiscoveryStartForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                discovery = start_discovery(
                    form.cleaned_data["objective"],
                    constraints=form.cleaned_data["constraints"],
                    config=form.discovery_config(),
                    idempotency_key=str(form.cleaned_data["idempotency_key"]),
                )
            except _FORM_ERRORS as exc:  # allow: suppress-exception
                # Expected form/configuration conflicts remain visible to the operator.
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Discovery queued. Finder will research first.")
                return HttpResponseRedirect(reverse("admin:discovery-review", args=[discovery.id]))
        return render(
            request,
            "admin/discovery/start.html",
            {
                **self.admin_site.each_context(request),
                "title": "Start an opportunity discovery",
                "opts": self.model._meta,
                "form": form,
                "connector_status": describe_connectors(),
            },
        )

    def review_view(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        if not self.has_view_or_change_permission(request, discovery):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        evidence = [
            {
                "id": item.id,
                "url": item.url,
                "public_url": _url(item.url),
                "title": item.title,
                "kind": item.kind,
                "connector": item.connector,
                "retrieved_at": item.retrieved_at,
                "content": item.content,
            }
            for item in discovery.evidence.order_by("retrieved_at", "id")
        ]
        benchmark_objective = (
            next(
                (
                    item
                    for item in load_benchmark().objectives
                    if item.id == discovery.benchmark_objective_id
                ),
                None,
            )
            if discovery.benchmark_objective_id
            else None
        )
        turns = [
            {"turn": turn, "formatted_submission": json.dumps(turn.submission, indent=2)}
            for turn in discovery.turns.order_by("number")
        ]
        return render(
            request,
            "admin/discovery/review.html",
            {
                **self.admin_site.each_context(request),
                "title": "Opportunity discovery",
                "opts": self.model._meta,
                "discovery": discovery,
                "mail": discovery.messages.order_by("created_at", "id"),
                "turns": turns,
                "judgments": [
                    {"judgment": row, "formatted_assessment": json.dumps(row.content, indent=2)}
                    for row in discovery.judgments.select_related("turn", "proposal").order_by(
                        "turn__number"
                    )
                ],
                "evidence": evidence,
                "operations": discovery.operations.order_by("created_at", "id"),
                "config_json": json.dumps(discovery.config_snapshot, indent=2),
                # Model prose and source URLs are untrusted even in our own report.
                "report_html": mark_safe(
                    bleach.clean(
                        markdown.markdown(
                            discovery.report_markdown, extensions=["tables", "fenced_code"]
                        ),
                        tags={
                            "h1",
                            "h2",
                            "h3",
                            "h4",
                            "p",
                            "ul",
                            "ol",
                            "li",
                            "strong",
                            "em",
                            "blockquote",
                            "code",
                            "pre",
                            "a",
                            "hr",
                            "table",
                            "thead",
                            "tbody",
                            "tr",
                            "th",
                            "td",
                            "br",
                        },
                        attributes={"a": ["href", "title"]},
                        protocols={"http", "https"},
                        strip=True,
                    )
                ),
                "execution_profiles": [
                    {
                        "role": role.title(),
                        "provider": discovery.config_snapshot.get(f"{role}_provider", "openrouter"),
                        "model": discovery.config_snapshot.get(f"{role}_model", ""),
                    }
                    for role in (
                        ("finder", "validator", "judge")
                        if discovery.config_snapshot.get("judge_enabled", False)
                        else ("finder", "validator")
                    )
                ],
                "judge_required": discovery.config_snapshot.get("judge_enabled", False),
                "runtime_name": discovery.config_snapshot.get("runtime", "openrouter_batch"),
                "active": discovery.status in {"queued", "running"},
                "can_cancel": self.has_change_permission(request, discovery),
                "can_record_verdict": discovery.status in TERMINAL_STATUSES
                and self.has_change_permission(request, discovery),
                "verdict_form": VerdictForm(),
                "benchmark_objective": benchmark_objective,
                "verdicts": discovery.verdicts.select_related("recorded_by").order_by(
                    "-recorded_at", "-id"
                ),
            },
        )

    def report_view(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        if not self.has_view_or_change_permission(request, discovery):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        if not discovery.report_markdown:
            messages.warning(request, "The report is not ready yet.")
            return HttpResponseRedirect(reverse("admin:discovery-review", args=[discovery.id]))
        return report_response(discovery)

    def verdict_view(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        if not self.has_change_permission(request, discovery):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        review = HttpResponseRedirect(reverse("admin:discovery-review", args=[discovery.id]))
        form = VerdictForm(request.POST)
        if not form.is_valid():
            for error in form.errors.values():
                messages.error(request, " ".join(str(message) for message in error))
            return review
        try:
            record_verdict(
                discovery,
                form.cleaned_data["verdict"],
                form.cleaned_data["notes"],
                user=request.user,
                via="admin",
            )
        except VerdictNotAllowed as exc:  # allow: suppress-exception
            # Shown to the operator; nothing was written.
            messages.error(request, str(exc))
            return review
        messages.success(request, "Verdict recorded and report regenerated.")
        return review

    def cancel_view(self, request, discovery_id):
        discovery = get_object_or_404(Discovery, pk=discovery_id)
        if not self.has_change_permission(request, discovery):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        cancel_discovery(discovery.id)
        messages.info(
            request, "Discovery stopped. Any in-flight provider work may still be billed."
        )
        return HttpResponseRedirect(reverse("admin:discovery-review", args=[discovery.id]))
