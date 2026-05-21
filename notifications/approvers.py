"""Approver interface.

Approvers are the pluggable surface for "tell a human there is something to
look at." The default ``DjangoAdminApprover`` is pull-based (humans go to
admin) and serves as the no-op baseline. Slack, email, Telegram approvers
can be added later by implementing the same protocol and wiring the
``APPROVER`` env var.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import import_module
from uuid import UUID

from django.conf import settings

from investigations.models import Investigation, InvestigationStatus


class Approver(ABC):
    @abstractmethod
    def notify_pending(self, investigation_id: UUID) -> None:
        """Tell a human a new investigation needs review."""

    @abstractmethod
    def get_pending_count(self) -> int:
        """How many investigations are currently awaiting review."""


class DjangoAdminApprover(Approver):
    """Default — Django admin is pull-based, so notification is a no-op."""

    def notify_pending(self, investigation_id: UUID) -> None:  # noqa: ARG002
        return None

    def get_pending_count(self) -> int:
        return Investigation.objects.filter(status=InvestigationStatus.AWAITING_REVIEW).count()


def get_approver() -> Approver:
    """Resolve the configured approver class. Imports lazily."""
    dotted = settings.APPROVER
    module_path, _, attr = dotted.rpartition(".")
    cls = getattr(import_module(module_path), attr)
    return cls()
