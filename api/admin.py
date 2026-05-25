"""ApiKey admin — create + revoke. Raw key is shown once via flash message."""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from api.models import ApiKey


class ApiKeyCreateForm(forms.Form):
    label = forms.CharField(max_length=64, help_text="e.g. 'facundo-laptop'.")


@admin.register(ApiKey)
class ApiKeyAdmin(UnfoldModelAdmin):
    """Mint + revoke keys.

    Edit fields are read-only to prevent accidental rotation via the change
    form — a rotated ``key_hash`` would be unreachable since the plaintext
    isn't stored anywhere. To rotate, revoke + create a new one.
    """

    list_display = ("label", "prefix_display", "user", "created_at", "last_used_at", "status")
    list_filter = ("user",)
    search_fields = ("label", "prefix", "user__username")
    actions = ["revoke_selected"]
    readonly_fields = ("id", "key_hash", "prefix", "created_at", "last_used_at", "revoked_at")

    @admin.display(description="Prefix")
    def prefix_display(self, obj: ApiKey) -> str:
        return f"{obj.prefix}…"

    @admin.display(description="Status")
    def status(self, obj: ApiKey) -> str:
        return "active" if obj.is_active else "revoked"

    @admin.action(description="Revoke selected keys")
    def revoke_selected(self, request, queryset):
        n = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        messages.success(request, f"Revoked {n} key(s).")

    def get_urls(self):
        return [
            path(
                "create-key/",
                self.admin_site.admin_view(self.create_key_view),
                name="api-apikey-create",
            ),
        ] + super().get_urls()

    def create_key_view(self, request):
        """Mint a key for ``request.user`` and flash the raw value once.

        Custom view (not the standard add form) because the standard form
        would expose ``key_hash`` and ``prefix`` as editable fields. The
        operator only supplies ``label`` — everything else is generated.
        """
        if request.method == "POST":
            form = ApiKeyCreateForm(request.POST)
            if form.is_valid():
                row, raw = ApiKey.create_for_user(
                    user=request.user, label=form.cleaned_data["label"]
                )
                messages.success(
                    request,
                    format_html(
                        "<strong>Save this now — it won't be shown again:</strong> "
                        "<code style='background:#1e293b;color:#fef3c7;padding:4px 8px;"
                        "border-radius:4px;font-size:0.95em;'>{}</code> "
                        "(label: {}, prefix: {}…)",
                        raw,
                        row.label,
                        row.prefix,
                    ),
                )
                return HttpResponseRedirect("/admin/api/apikey/")
        else:
            form = ApiKeyCreateForm()

        return render(
            request,
            "admin/api/apikey/create.html",
            {
                **self.admin_site.each_context(request),
                "title": "Create API key",
                "form": form,
            },
        )
