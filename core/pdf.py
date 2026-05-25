"""PDF rendering — shared helper for the download buttons.

Wraps WeasyPrint behind a one-line API so the views (admin + REST) and
their tests only see ``render_pdf(template, context) -> bytes``.

WeasyPrint imports cleanly without Pango/Cairo installed but fails at
the first render call. The Dockerfile installs the system libs; tests
that touch render_pdf must run in the container, not the host venv.
"""

from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string

# Import is deferred so that test environments without the WeasyPrint
# system libs can still import this module — the failure surfaces only
# when ``render_pdf`` is actually called. Inside the container the libs
# are present, so the deferred import resolves immediately.


def render_pdf(template_name: str, context: dict[str, Any]) -> bytes:
    """Render ``template_name`` with ``context`` and return PDF bytes.

    Raises whatever WeasyPrint raises on broken templates / missing
    system libs. Callers wrap with the appropriate HTTP response — no
    fallback bytes, no swallowed errors.
    """
    from weasyprint import HTML  # noqa: PLC0415  # deferred — see module docstring

    html = render_to_string(template_name, context)
    return HTML(string=html).write_pdf()
