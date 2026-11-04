"""Operator interface for starting and inspecting two-agent discoveries."""

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
from discovery.models import Discovery
from discovery.orchestrator import DiscoveryConflict, cancel_discovery, start_discovery
from discovery.schemas import DEFAULT_MODEL

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
        self.fields["idempotency_key"].initial = uuid.uuid4()

    def discovery_config(self):
        keys = (
            "max_turns",
            "max_cost_usd",
            "max_duration_s",
            "max_model_calls_per_turn",
            "max_research_calls_per_turn",
            "finder_provider",
            "finder_model",
            "validator_provider",
            "validator_model",
            "connectors",
        )
        return {
            "schema_version": "1.1",
            "runtime": "claude_code",
            **{key: self.cleaned_data[key] for key in keys},
        }


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
    list_filter = ("status", "outcome", "stop_reason")
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
                "evidence": evidence,
                "operations": discovery.operations.order_by("created_at", "id"),
                "config_json": json.dumps(discovery.config_snapshot, indent=2),
                # Model prose and source URLs are untrusted even in our own report.
                "report_html": mark_safe(
                    bleach.clean(
                        markdown.markdown(discovery.report_markdown, extensions=["tables"]),
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
                    for role in ("finder", "validator")
                ],
                "runtime_name": discovery.config_snapshot.get("runtime", "openrouter_batch"),
                "active": discovery.status in {"queued", "running"},
                "can_cancel": self.has_change_permission(request, discovery),
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
