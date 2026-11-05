"""Backfill of source types: dry run writes nothing, --apply is idempotent."""

from io import StringIO

import pytest
from django.core.management import call_command

from discovery.models import Evidence
from discovery.source_types import load_rules
from tests.discovery_fixtures import discovery, evidence, turn


def _run(*args: str) -> str:
    out = StringIO()
    call_command("classify_evidence", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_backfill_dry_run_then_apply_is_idempotent():
    step = turn(discovery(), 1, "finder")
    pre_phase = [
        evidence(step, "indec.gob.ar", url="https://www.indec.gob.ar/x"),
        evidence(step, "acme.com", url="https://acme.com/pricing"),
        evidence(step, "acme.com", url="https://acme.com/about", text="About"),
    ]
    current = evidence(step, "reddit.com", url="https://reddit.com/r/x", source_type="forum")
    current.metadata = {
        "source_type_rule": "domain:reddit.com",
        "source_type_rules_version": load_rules().rules_version,
    }
    current.save()

    output = _run()
    assert "Dry run (nothing written)" in output
    assert "3 row(s) classified under other rules; 2 would change type" in output
    assert "-> statistics: 1" in output and "-> vendor: 1" in output
    assert set(Evidence.objects.values_list("source_type", flat=True)) == {
        "unclassified",
        "forum",
    }
    for row in pre_phase:
        row.refresh_from_db()
        assert "source_type_rule" not in row.metadata

    output = _run("--apply")
    assert "Applied" in output and "2 changed type" in output
    types = {row.url: row for row in Evidence.objects.all()}
    assert types["https://www.indec.gob.ar/x"].source_type == "statistics"
    assert types["https://acme.com/pricing"].source_type == "vendor"
    about = types["https://acme.com/about"]
    assert about.source_type == "unclassified"
    assert about.metadata["source_type_rule"] == "default"
    assert about.metadata["source_type_rules_version"] == load_rules().rules_version
    assert about.schema_version == "1.0"

    assert "0 row(s) classified under other rules; 0 changed type" in _run("--apply")
