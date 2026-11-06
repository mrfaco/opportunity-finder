"""Phase 2 defaults and the connectors endpoint: keyless defaults start; status never leaks keys."""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from api.models import ApiKey
from discovery.connectors.registry import describe_connectors
from discovery.orchestrator import resolve_config, start_discovery
from discovery.schemas import DEFAULT_CONNECTORS, DiscoveryConfig

DEFAULT = ["search_auto", "web_fetch", "reddit", "news_gdelt", "open_data_ckan", "wayback"]
NEW_CONNECTORS = {"reddit", "news_gdelt", "open_data_ckan", "wayback", "brave", "jobs_adzuna"}
NO_KEYS = {
    "BRAVE_SEARCH_API_KEY": "",
    "ADZUNA_APP_ID": "",
    "ADZUNA_APP_KEY": "",
    "REDDIT_CLIENT_ID": "",
    "REDDIT_CLIENT_SECRET": "",
    "TAVILY_API_KEY": "",
    "GITHUB_TOKEN": "",
}


def test_default_connector_list_is_the_six_keyless_sources_everywhere():
    assert settings.DISCOVERY_CONNECTORS == DEFAULT
    assert list(DEFAULT_CONNECTORS) == DEFAULT
    assert DiscoveryConfig().connectors == DEFAULT
    assert resolve_config(None).connectors == DEFAULT


@pytest.mark.django_db
@override_settings(**NO_KEYS)
def test_a_default_discovery_starts_without_any_key_and_snapshots_the_new_connectors():
    with patch("discovery.runtime.check_configuration", return_value={"profiles": {}}):
        row = start_discovery("Find a services business in Argentina", enqueue=False)
    assert row.config_snapshot["connectors"] == DEFAULT
    # A historical snapshot keeps the list it was started with.
    assert DiscoveryConfig.model_validate(
        {"connectors": ["search_auto", "web_fetch"]}
    ).connectors == ["search_auto", "web_fetch"]


@pytest.mark.django_db
@override_settings(**NO_KEYS)
def test_connectors_endpoint_lists_every_connector_with_configuration_status(
    client, django_user_model
):
    user = django_user_model.objects.create_user(username="connectors", password="pass")
    _, raw = ApiKey.create_for_user(user=user, label="test")
    response = client.get("/api/v1/connectors/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    by_id = {row["id"]: row for row in body["results"]}
    assert set(by_id) == {row["id"] for row in describe_connectors()}
    assert NEW_CONNECTORS <= set(by_id)
    for connector_id in DEFAULT:
        assert by_id[connector_id]["configured"] is True, connector_id
    assert by_id["brave"]["configured"] is False
    assert by_id["brave"]["configuration_error"] == "BRAVE_SEARCH_API_KEY is not configured"
    assert by_id["jobs_adzuna"]["configuration_requirements"] == ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"]
    assert "datos.gob.ar" in by_id["open_data_ckan"]["name"]
    for row in body["results"]:
        assert {"configured", "configuration_error", "supported_filters", "access_limitations"} <= (
            set(row)
        )


@pytest.mark.django_db
@override_settings(
    BRAVE_SEARCH_API_KEY="secret-brave",
    ADZUNA_APP_ID="secret-id",
    ADZUNA_APP_KEY="secret-adzuna",
    REDDIT_CLIENT_ID="secret-reddit-id",
    REDDIT_CLIENT_SECRET="secret-reddit",
)
def test_configured_keys_show_as_configured_and_never_appear(client, django_user_model):
    user = django_user_model.objects.create_user(username="keys", password="pass")
    _, raw = ApiKey.create_for_user(user=user, label="test")
    text = client.get("/api/v1/connectors/", HTTP_AUTHORIZATION=f"Bearer {raw}").content.decode()
    assert "secret-" not in text
    by_id = {row["id"]: row for row in describe_connectors()}
    assert by_id["brave"]["configured"] and by_id["jobs_adzuna"]["configured"]
    assert "OAuth app" in by_id["reddit"]["name"]
