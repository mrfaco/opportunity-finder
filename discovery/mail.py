"""Discovery-scoped internal mail. Reading never completes assigned work."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from discovery.models import DiscoveryMessage, DiscoveryTask

PARTIES = {"human", "system", "finder", "validator"}


def send_message(
    *,
    discovery_id: UUID | str,
    sender: str,
    recipient: str,
    kind: str,
    subject: str,
    body: str,
    operation_key: str,
    task: DiscoveryTask | None = None,
    reply_to: DiscoveryMessage | None = None,
    artifact_id: UUID | str | None = None,
) -> DiscoveryMessage:
    if sender not in PARTIES or recipient not in PARTIES or sender == recipient:
        raise ValueError("Invalid discovery mail parties")
    if kind not in {"request", "reply", "note"}:
        raise ValueError("Invalid message kind")
    if not subject.strip() or len(subject) > 200 or not body.strip():
        raise ValueError("Mail requires a subject (at most 200 characters) and body")
    if len(body.encode("utf-8")) > 65536:
        raise ValueError("Mail body exceeds 64 KiB; reference an artifact instead")
    if kind == "reply" and reply_to is None:
        raise ValueError("A reply needs a parent message")
    if task is not None and str(task.discovery_id) != str(discovery_id):
        raise ValueError("Task belongs to another discovery")
    if reply_to is not None and str(reply_to.discovery_id) != str(discovery_id):
        raise ValueError("Reply belongs to another discovery")
    values = {
        "sender": sender,
        "recipient": recipient,
        "kind": kind,
        "subject": subject,
        "body": body,
        "task": task,
        "reply_to": reply_to,
        "artifact_id": artifact_id,
    }
    message, created = DiscoveryMessage.objects.get_or_create(
        discovery_id=discovery_id,
        operation_key=operation_key,
        defaults=values,
    )
    if not created:
        for name, value in values.items():
            existing = getattr(message, name)
            if existing != value:
                raise ValueError("Message operation key reused with different content")
    return message


def inbox(discovery_id: UUID | str, recipient: str) -> list[DiscoveryMessage]:
    if recipient not in PARTIES:
        raise ValueError("Unknown recipient")
    with transaction.atomic():
        messages = list(
            DiscoveryMessage.objects.select_for_update().filter(
                discovery_id=discovery_id,
                recipient=recipient,
                read_at__isnull=True,
            )
        )
        now = timezone.now()
        DiscoveryMessage.objects.filter(pk__in=[m.pk for m in messages]).update(read_at=now)
        for message in messages:
            message.read_at = now
    return messages


def show(discovery_id: UUID | str, message_id: UUID | str) -> DiscoveryMessage:
    return DiscoveryMessage.objects.get(pk=message_id, discovery_id=discovery_id)


def log(discovery_id: UUID | str, limit: int = 100) -> list[dict[str, Any]]:
    if limit < 1 or limit > 500:
        raise ValueError("Mail log limit must be between 1 and 500")
    return [
        dict(row)
        for row in (
            DiscoveryMessage.objects.filter(discovery_id=discovery_id).values(
                "id",
                "sender",
                "recipient",
                "kind",
                "subject",
                "body",
                "artifact_id",
                "reply_to_id",
                "created_at",
                "read_at",
            )[:limit]
        )
    ]
