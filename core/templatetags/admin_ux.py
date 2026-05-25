"""Template tags shared across the admin UX.

- ``json_pretty``: if the value is a JSON string, pretty-print it; otherwise
  return it unchanged. Must never raise — admin pages cannot 500 because a
  ``tool_input`` field happens to be malformed JSON.
- ``status_tone``: map a status string (case-insensitive) to a badge tone
  consumed by ``admin/_ux/_badge.html``.
"""

from __future__ import annotations

import json

from django import template

register = template.Library()


_TONE_BY_STATUS = {
    # success
    "completed": "success",
    "success": "success",
    "approved": "success",
    "promoted": "success",
    # danger
    "failed": "danger",
    "error": "danger",
    "rejected": "danger",
    "discarded": "danger",
    # info
    "running": "info",
    "in_progress": "info",
    "investigating": "info",
    # warning
    "pending": "warning",
    "queued": "warning",
    "review_pending": "warning",
}


@register.filter(name="json_pretty")
def json_pretty(value):
    """Pretty-print a JSON string; pass non-JSON through unchanged.

    Never raises — admin pages must keep rendering even with malformed input.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):  # allow: suppress-exception
        # Intentional fallthrough: malformed JSON must not 500 an admin page.
        return value
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)


@register.filter(name="status_tone")
def status_tone(value) -> str:
    """Map a status string to a badge tone. Defaults to ``"neutral"``."""
    if not value:
        return "neutral"
    return _TONE_BY_STATUS.get(str(value).lower(), "neutral")
