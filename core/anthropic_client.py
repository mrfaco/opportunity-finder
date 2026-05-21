"""Shared Anthropic SDK client.

A single factory so every caller (filter classifier now, investigation loop
later) constructs the client the same way and reads the key from one place.
"""

from __future__ import annotations

import anthropic
from django.conf import settings


def get_client() -> anthropic.Anthropic:
    """Return an Anthropic client.

    Raises ``RuntimeError`` if no API key is configured — we fail loudly
    rather than letting the SDK surface a less obvious auth error deep in a
    request.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Configure it in .env before "
            "running the classifier or the investigation agent."
        )
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
