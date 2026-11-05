"""Publication dates from page markup, HTTP headers and PDFs, and their report rendering."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from discovery.connectors.base import parse_stated_date
from discovery.connectors.pdf import extract_pdf
from discovery.connectors.web import _published_at, fetch_public_url
from discovery.reporting import render_report
from discovery.termination import evidence_gaps
from tests.discovery_fixtures import discovery, evidence, pdf_bytes, proposal, turn

ARTICLE = '<meta property="article:published_time" content="2024-03-01T08:00:00+00:00">'
META = '<meta name="DC.date" content="2023-02-01">'
JSON_LD = (
    '<script type="application/ld+json">{"@graph": [{"@type": "Organization"}, '
    '{"@type": ["NewsArticle"], "datePublished": "2022-01-05T10:00:00Z"}]}</script>'
)
TIME = '<time pubdate datetime="2021-06-07">7 June</time>'
LAST_MODIFIED = {"Last-Modified": "Wed, 01 May 2019 12:00:00 GMT"}


@pytest.mark.parametrize(
    ("html", "headers", "expected"),
    [
        (ARTICLE + META + JSON_LD + TIME, LAST_MODIFIED, ("2024-03-01", "meta_article_published")),
        (META + JSON_LD + TIME, LAST_MODIFIED, ("2023-02-01", "meta_date")),
        (JSON_LD + TIME, LAST_MODIFIED, ("2022-01-05", "json_ld_date_published")),
        (TIME, LAST_MODIFIED, ("2021-06-07", "time_date_published")),
        ('<time itemprop="datePublished" datetime="2020-01-01">', {}, ("2020-01-01", None)),
        ("<p>No dates</p>", LAST_MODIFIED, ("2019-05-01", "http_last_modified")),
    ],
)
def test_date_sources_in_precedence_order(html, headers, expected):
    stated = _published_at(html, headers)
    assert stated is not None
    assert stated[0].startswith(expected[0])
    assert stated[1] == (expected[1] or "time_date_published")


def test_malformed_future_and_missing_dates_are_ignored():
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    html = (
        f'<meta property="article:published_time" content="{future}">'
        '<meta name="date" content="last Tuesday">'
        '<script type="application/ld+json">{not json</script>'
        '<time datetime="2020-01-01">not marked as publication</time>'
    )
    assert _published_at(html, {}) is None
    # An invalid stronger source falls through to a valid weaker one.
    assert _published_at(html, LAST_MODIFIED) == (
        "2019-05-01T12:00:00+00:00",
        "http_last_modified",
    )
    assert parse_stated_date("2024-13-45") is None
    assert parse_stated_date("") is None
    tomorrow_ish = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    assert parse_stated_date(tomorrow_ish) is not None


def _response(body: bytes, headers: dict[str, str]) -> MagicMock:
    response = MagicMock(status=200)
    response.getheader.side_effect = headers.get
    response.read.side_effect = BytesIO(body).read
    return response


def _fetch(response: MagicMock):
    connection = MagicMock()
    connection.getresponse.return_value = response
    with (
        patch(
            "discovery.connectors.web.public_url_address",
            return_value=("https", "news.example", 443, "93.184.216.34"),
        ),
        patch("discovery.connectors.web.http.client.HTTPSConnection", return_value=connection),
    ):
        return fetch_public_url("https://news.example/story")


def test_fetched_html_records_published_at_and_its_source():
    page = _fetch(
        _response(
            f"<html><head>{ARTICLE}</head><body>Story</body></html>".encode(),
            {"Content-Type": "text/html; charset=utf-8", **LAST_MODIFIED},
        )
    )
    document = page.documents[0]
    assert document.published_at == "2024-03-01T08:00:00+00:00"
    assert document.metadata["published_at_source"] == "meta_article_published"

    page = _fetch(_response(b"plain text", {"Content-Type": "text/plain", **LAST_MODIFIED}))
    assert page.documents[0].metadata["published_at_source"] == "http_last_modified"

    page = _fetch(_response(b"<p>undated</p>", {"Content-Type": "text/html"}))
    assert page.documents[0].published_at is None
    assert "published_at_source" not in page.documents[0].metadata


def test_pdf_creation_then_modification_date():
    created = extract_pdf(
        pdf_bytes(["Annex"], {"CreationDate": "D:20230415090000Z", "ModDate": "D:20240101Z"}),
        "https://regulator.example/a.pdf",
        "application/pdf",
    ).documents[0]
    assert created.published_at == "2023-04-15T09:00:00+00:00"
    assert created.metadata["published_at_source"] == "pdf_creation"
    modified = extract_pdf(
        pdf_bytes(["Annex"], {"ModDate": "D:20240102030405Z"}),
        "https://regulator.example/b.pdf",
        "application/pdf",
    ).documents[0]
    assert modified.published_at == "2024-01-02T03:04:05+00:00"
    assert modified.metadata["published_at_source"] == "pdf_modified"
    undated = extract_pdf(
        pdf_bytes(["Annex"]), "https://regulator.example/c.pdf", "application/pdf"
    ).documents[0]
    assert undated.published_at is None


@pytest.mark.django_db
def test_report_prints_age_and_lists_stale_cited_evidence_without_blocking():
    row = discovery()
    step = turn(row, 1, "finder")
    old = evidence(step, "old.example", age_days=913)
    old.metadata = {"published_at_source": "http_last_modified"}
    old.save()
    fresh = evidence(step, "fresh.example", age_days=40)
    fresh.metadata = {"published_at_source": "meta_article_published"}
    fresh.save()
    uncited = evidence(step, "uncited.example", age_days=2000)
    final = proposal(
        step,
        {"demand_evidence_ids": [str(old.pk)], "competitor_evidence_ids": [str(fresh.pk)]},
    )
    assert evidence_gaps(row, final) == []

    markdown, report = render_report(row)
    assert "(http_last_modified, weak: server modification time); age 30 months" in markdown
    assert "(meta_article_published); age 1 months" in markdown
    stale = [line for line in report["limitations"] if "older than 24 months" in line]
    assert len(stale) == 1
    assert str(old.pk) in stale[0] and "(30 months)" in stale[0]
    assert str(uncited.pk) not in stale[0] and str(fresh.pk) not in stale[0]
    by_id = {item["id"]: item for item in report["evidence"]}
    assert by_id[str(old.pk)]["published_at_source"] == "http_last_modified"
    assert round(by_id[str(old.pk)]["age_months"]) == 30
