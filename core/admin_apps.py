"""Admin AppConfig that swaps in our custom admin site.

Kept in its own module so Django's ``default_app_config`` autodetection
doesn't see two candidate ``AppConfig`` classes in ``core/apps.py``.
"""

from __future__ import annotations

from django.contrib.admin.apps import AdminConfig


class PainMinerAdminConfig(AdminConfig):
    default_site = "core.admin_site.PainMinerAdminSite"
