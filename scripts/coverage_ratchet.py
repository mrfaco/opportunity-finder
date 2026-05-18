"""Bump the coverage gate up to the floor of the current run.

Reads ``coverage.xml`` (produced by ``pytest --cov-report=xml``), extracts
the line-rate, floors it to an integer, and rewrites the
``--cov-fail-under=N`` flag inside ``pyproject.toml`` if N exceeds the
current threshold. Never lowers the threshold — the whole point of the
ratchet is that it only moves up.

Usage::

    make coverage         # run tests + emit coverage.xml
    make coverage-ratchet # apply the ratchet (commits with the change)
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
COVERAGE_XML = Path("coverage.xml")
PATTERN = re.compile(r"--cov-fail-under=(\d+)")


def main() -> int:
    if not COVERAGE_XML.exists():
        print(f"{COVERAGE_XML} not found — run `make coverage` first.", file=sys.stderr)
        return 2
    tree = ET.parse(COVERAGE_XML)
    line_rate = float(tree.getroot().get("line-rate", "0"))
    new_floor = int(line_rate * 100)

    text = PYPROJECT.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if match is None:
        print(
            "Did not find --cov-fail-under=N in pyproject.toml; bail out so we "
            "don't accidentally introduce a different addopts shape.",
            file=sys.stderr,
        )
        return 2
    current = int(match.group(1))

    if new_floor <= current:
        print(f"Coverage {new_floor}% does not exceed current floor {current}%. No change.")
        return 0

    updated = PATTERN.sub(f"--cov-fail-under={new_floor}", text, count=1)
    PYPROJECT.write_text(updated, encoding="utf-8")
    print(f"Coverage ratchet: {current}% → {new_floor}%. Commit the pyproject.toml change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
