"""web_fetch 1.2 reads CSV open-data resources under the existing 200 KB bound."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from discovery.connectors.web import WebFetchConnector, fetch_public_url


def _fetch(url: str, body: bytes, content_type: str):
    response = MagicMock(status=200)
    response.getheader.side_effect = {"Content-Type": content_type}.get
    response.read.side_effect = BytesIO(body).read
    connection = MagicMock()
    connection.getresponse.return_value = response
    with (
        patch(
            "discovery.connectors.web.public_url_address",
            return_value=("https", "datos.gob.ar", 443, "200.0.0.1"),
        ),
        patch("discovery.connectors.web.http.client.HTTPSConnection", return_value=connection),
    ):
        return fetch_public_url(url)


@pytest.mark.parametrize(
    ("url", "content_type"),
    [
        ("https://datos.gob.ar/dataset/precios", "text/csv; charset=utf-8"),
        ("https://datos.gob.ar/dataset/precios", "application/csv"),
        ("https://datos.gob.ar/files/precios.csv", "application/octet-stream"),
    ],
)
def test_csv_is_returned_as_text_with_format_and_header_columns(url, content_type):
    body = '﻿provincia,"precio medio",fecha\nCordoba,1200,2026-01\n'.encode()
    page = _fetch(url, body, content_type)
    document = page.documents[0]
    assert page.status == "success" and not page.truncated
    assert document.connector_version == "1.2"
    assert document.metadata["format"] == "csv"
    assert document.metadata["columns"] == ["provincia", "precio medio", "fecha"]
    assert "Cordoba,1200" in document.text
    assert document.title == url


def test_large_csv_is_truncated_and_flagged():
    body = b"a,b\n" + b"1,2\n" * 60_000
    page = _fetch("https://datos.gob.ar/big.csv", body, "text/csv")
    assert page.truncated and page.documents[0].metadata["truncated"] is True
    assert page.documents[0].metadata["columns"] == ["a", "b"]
    assert len(page.documents[0].text) == 200_000


def test_octet_stream_without_csv_path_is_still_refused():
    result = WebFetchConnector()
    with patch(
        "discovery.connectors.web.fetch_public_url",
        wraps=lambda url: _fetch(url, b"\x00\x01binary", "application/octet-stream"),
    ):
        page = result.fetch("https://datos.gob.ar/files/blob.bin")
    assert page.status == "error" and "not a PDF or CSV" in page.error_reason


def test_html_capture_has_no_csv_metadata_and_version_is_1_2():
    page = _fetch("https://datos.gob.ar/", b"<title>Datos</title><p>x</p>", "text/html")
    assert "format" not in page.documents[0].metadata
    assert WebFetchConnector().describe().version == "1.2"
    assert "CSV" in WebFetchConnector().describe().access_limitations
