"""Re-export task entry points so Celery autodiscovery finds them."""

from notifications.digests import send_daily_review_digest

__all__ = ["send_daily_review_digest"]
