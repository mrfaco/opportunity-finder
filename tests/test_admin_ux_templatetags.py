"""Unit tests for the admin_ux template tag library."""

from __future__ import annotations

import json

import pytest

from core.templatetags.admin_ux import json_pretty, status_tone


class TestJsonPretty:
    def test_dict_is_pretty_printed(self):
        raw = json.dumps({"b": 2, "a": 1})
        out = json_pretty(raw)
        assert out.startswith("{\n")
        assert '"a": 1' in out
        assert '"b": 2' in out

    def test_list_is_pretty_printed(self):
        raw = json.dumps([1, 2, 3])
        out = json_pretty(raw)
        assert out.startswith("[\n")
        assert "  1," in out

    def test_non_json_string_passes_through(self):
        out = json_pretty("not json {at all")
        assert out == "not json {at all"

    def test_empty_string_passes_through(self):
        assert json_pretty("") == ""

    def test_none_passes_through(self):
        assert json_pretty(None) is None

    def test_non_string_passes_through(self):
        assert json_pretty(42) == 42


class TestStatusTone:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("completed", "success"),
            ("success", "success"),
            ("approved", "success"),
            ("failed", "danger"),
            ("error", "danger"),
            ("rejected", "danger"),
            ("running", "info"),
            ("in_progress", "info"),
            ("pending", "warning"),
            ("queued", "warning"),
            ("draft", "neutral"),
            ("unknown_value", "neutral"),
            ("", "neutral"),
            (None, "neutral"),
        ],
    )
    def test_known_and_unknown_statuses(self, status, expected):
        assert status_tone(status) == expected

    def test_case_insensitive(self):
        assert status_tone("COMPLETED") == "success"
        assert status_tone("Failed") == "danger"
