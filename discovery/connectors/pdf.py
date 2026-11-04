"""Bounded Poppler extraction with page-addressable evidence."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from discovery.connectors.base import ConnectorError, ResearchDocument, ResearchPage

PDF_MAX_BYTES = 20_000_000
_MAX_PAGES = 10
_MAX_TEXT = 120_000


def extract_pdf(raw: bytes, url: str, content_type: str) -> ResearchPage:
    fragment = urlsplit(url).fragment
    match = re.fullmatch(r"page=(\d+)(?:-(\d+))?", fragment) if fragment else None
    if fragment and not match:
        raise ConnectorError("PDF page selection must use #page=N or #page=N-M (at most 10 pages)")
    first = int(match[1]) if match else 1
    last = int(match[2] or match[1]) if match else _MAX_PAGES
    if first < 1 or last < first or last - first >= _MAX_PAGES:
        raise ConnectorError("PDF page range must contain 1–10 pages, starting at page 1 or later")
    with tempfile.TemporaryDirectory(prefix="opportunity-pdf-") as directory:
        source = Path(directory) / "source.pdf"
        output = Path(directory) / "text.txt"
        source.write_bytes(raw)
        info = subprocess.run(
            ["pdfinfo", str(source)], capture_output=True, timeout=15, check=False
        )
        if info.returncode:
            raise ConnectorError("PDF is corrupt, encrypted, or unsupported by Poppler")
        pages = re.search(rb"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
        if not pages:
            raise ConnectorError("PDF page count unavailable")
        count = int(pages[1])
        if first > count:
            raise ConnectorError(f"PDF has only {count} pages")
        last = min(last, count)
        result = subprocess.run(
            [
                "pdftotext",
                "-layout",
                "-enc",
                "UTF-8",
                "-f",
                str(first),
                "-l",
                str(last),
                str(source),
                str(output),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise ConnectorError("PDF text extraction failed")
        with output.open("rb") as stream:
            text = stream.read(_MAX_TEXT + 1).decode("utf-8", errors="replace")
    truncated = len(text.encode("utf-8")) > _MAX_TEXT
    text = text[:_MAX_TEXT]
    chunks = text.split("\f")
    base = urlunsplit(urlsplit(url)._replace(fragment=""))
    next_page = first + min(len(chunks), last - first + 1) - (1 if truncated else 0)
    documents = []
    for number, body in enumerate(chunks[: last - first + 1], first):
        if not body.strip():
            continue
        page_url = f"{base}#page={number}"
        documents.append(
            ResearchDocument(
                connector="web_fetch",
                connector_version="1.1",
                external_id=page_url,
                url=page_url,
                title=f"{base} — page {number}",
                text=f"[PDF page {number} of {count}]\n{body.strip()}",
                kind="fetched_page",
                metadata={
                    "format": "pdf",
                    "content_type": content_type,
                    "page": number,
                    "page_count": count,
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "extraction": "poppler-layout",
                    "truncated": truncated,
                    "selected_pages": [first, last],
                    "next_page_url": f"{base}#page={next_page}-{min(next_page + 9, count)}"
                    if next_page <= count
                    else None,
                },
            )
        )
    if not documents:
        return ResearchPage(
            status="error",
            error_reason=(
                "PDF selected pages contain no extractable text; scanned pages require OCR. "
                "No readable evidence was captured."
            ),
        )
    return ResearchPage(documents=documents, truncated=truncated or first > 1 or last < count)
