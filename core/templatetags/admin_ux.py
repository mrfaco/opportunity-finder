"""Template tags shared across the admin UX.

- ``json_pretty``: if the value is a JSON string OR a Python repr of a
  dict/list (single-quoted), pretty-print it; otherwise return it
  unchanged. Must never raise — admin pages cannot 500 because a
  ``tool_input`` field happens to be malformed.
- ``status_tone``: map a status string (case-insensitive) to a badge tone
  consumed by ``admin/_ux/_badge.html``.
"""

from __future__ import annotations

import ast
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
def json_pretty(value: object) -> object:
    """Pretty-print a dict/list, JSON string, or Python-repr literal.

    Handles three shapes:
      - dict/list (e.g. from a JSONField) → ``json.dumps`` directly
      - JSON string → ``json.loads`` then dumps
      - Python-repr string (single-quoted) → ``ast.literal_eval`` then dumps

    Anything else passes through unchanged. Never raises — admin pages must
    keep rendering even with malformed input.
    """
    if value is None or value == "":
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(value, str):
        return value
    parsed: object = None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):  # allow: suppress-exception
        # Intentional fallthrough: try Python literal eval next.
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError, MemoryError, TypeError):  # allow: suppress-exception
            # Intentional fallthrough: malformed input must not 500 an admin page.
            return value
    return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True, default=str)


@register.filter(name="status_tone")
def status_tone(value: object) -> str:
    """Map a status string to a badge tone. Defaults to ``"neutral"``."""
    if not value:
        return "neutral"
    return _TONE_BY_STATUS.get(str(value).lower(), "neutral")
