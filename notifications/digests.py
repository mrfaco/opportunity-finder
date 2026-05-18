"""Daily review digest — emailed once per day with the pending review queue."""

from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from investigations.models import Investigation, InvestigationStatus


@shared_task
def send_daily_review_digest() -> dict:
    """Email a summary of pending investigations to the configured recipient.

    Recipient is ``EMAIL_DIGEST_RECIPIENT``. In dev, ``EMAIL_BACKEND`` defaults
    to the console backend so the email lands in the worker log.
    """
    recipient = settings.EMAIL_DIGEST_RECIPIENT
    if not recipient:
        return {"sent": 0, "reason": "no recipient configured"}

    pending = list(
        Investigation.objects.filter(status=InvestigationStatus.AWAITING_REVIEW)
        .select_related("cluster")
        .order_by("-created_at")[:50]
    )
    if not pending:
        return {"sent": 0, "reason": "no pending investigations"}

    # TODO(v1-followup): author the email body template
    # (notifications/templates/notifications/digest.txt). For now the body is
    # a minimal placeholder so the wiring works end-to-end.
    body = render_to_string("notifications/digest.txt", {"pending": pending})
    send_mail(
        subject=f"[painminer] {len(pending)} investigation(s) awaiting review",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )
    return {"sent": 1, "recipient": recipient, "count": len(pending)}
