"""The bounded JSON transport shared by API connectors: every provider failure is typed."""

import httpx
import pytest

from discovery.connectors.http import get_json, post_json
from tests.discovery_fixtures import TYPED_FAILURES, ProviderStub, json_reply


def test_get_json_parses_and_identifies_itself_without_following_redirects():
    stub = ProviderStub(json_reply({"items": [1, 2]}))
    with stub.patch():
        outcome = get_json(
            "https://api.provider.example/search",
            source="Provider",
            params={"q": "x"},
            headers={"X-Key": "k"},
        )
    assert outcome.error is None and outcome.data == {"items": [1, 2]}
    request = stub.requests[0]
    assert request["method"] == "GET"
    assert request["params"] == {"q": "x"}
    assert request["follow_redirects"] is False
    assert request["headers"]["User-Agent"].startswith("OpportunityFinder/")
    assert request["headers"]["X-Key"] == "k"


def test_post_json_sends_a_form_or_json_body():
    stub = ProviderStub(json_reply({"ok": True}), json_reply([]))
    with stub.patch():
        assert post_json("https://a.example/t", source="A", data={"g": "c"}).data == {"ok": True}
        assert post_json("https://a.example/s", source="A", json_body={"k": 1}).data == []
    assert stub.requests[0]["method"] == "POST" and stub.requests[0]["data"] == {"g": "c"}
    assert stub.requests[1]["json"] == {"k": 1}


@pytest.mark.parametrize(
    ("name", "reply", "status"), TYPED_FAILURES, ids=[x[0] for x in TYPED_FAILURES]
)
def test_each_provider_failure_is_a_typed_page_naming_the_source(name, reply, status):
    with ProviderStub(reply).patch():
        outcome = get_json("https://api.provider.example/x", source="Provider")
    assert outcome.data is None and outcome.error is not None
    assert outcome.error.status == status
    assert outcome.error.error_reason.startswith("Provider")
    if name == "rate_limited":
        assert outcome.error.retry_after_seconds == 30


def test_size_cap_is_enforced_while_streaming():
    chunks = [b"[" + b"1," * 600_000, b"1," * 600_000 + b"1]"]
    stream = httpx.Response(200, stream=httpx.ByteStream(b"".join(chunks)))
    with ProviderStub(stream).patch():
        outcome = get_json("https://api.provider.example/x", source="Provider")
    assert outcome.error is not None and "2 MB" in outcome.error.error_reason
