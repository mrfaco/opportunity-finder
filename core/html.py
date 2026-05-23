"""Minimal HTML→text utilities for fetched content.

Light-touch sanitiser used by the ingestion adapters and the fetch_url tool.
Not a full DOM-aware extractor — the use cases are short-form web text
(forum posts, news comments, Ask HN bodies) where regex-stripping plus
entity unescape produces clean-enough output without pulling in BeautifulSoup.
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """Flatten HTML to plain text.

    Drops ``<script>``/``<style>`` blocks entirely, turns paragraph and
    line-break tags into newlines, strips remaining tags, unescapes HTML
    entities, collapses repeated whitespace.
    """
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub("", raw)
    # Promote structural tags to line breaks before stripping.
    text = re.sub(r"</p>|<br\s*/?>|</li>|</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Tidy whitespace.
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
