"""Soft linter — flag new API endpoints that aren't documented in any skill.

Mirrors the existing ``check_api_parity.py`` (admin actions / Celery tasks →
API surface) with the *next* leg of the chain: new API endpoints → skill
markdown that operators read.

The PDF feature is the example that motivated this check. We added
``GET /api/v1/investigations/<id>/pdf/`` and ``GET /api/v1/ideations/<id>/pdf/``
but neither of the opp-* skills under ``.claude/skills/`` mentioned them, so
a skill user wouldn't know to call them.

What it detects:

* Lines added (in the **staged** diff) of the form ``path("<route>", …)``
  inside ``api/urls.py``. The URL fragment between the quotes is the
  signal we care about.

For each new route, it greps every ``.claude/skills/**/SKILL.md`` for the
URL fragment. If no skill mentions it, the route is "orphan" and the hook
nags.

Soft by default: prints warnings, exits 0. Pass ``--strict`` to exit
non-zero (useful from CI).

Run directly:

    python scripts/check_skill_parity.py            # scan staged diff
    python scripts/check_skill_parity.py --strict   # fail if any finding
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_URLS_PATH = "api/urls.py"
_SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"

# Diff lines: ``+    path("the/route/...",`` — the leading + is the marker.
# Single-line ``path(...)`` calls match this directly.
_ADDED_PATH_RE = re.compile(r'^\+\s*path\("([^"]+)"')

# When ``path(`` opens on one line and the URL string is on the *next* added
# line (multi-line ``path()`` blocks), we capture the route by combining a
# state flag (`saw_path_open`) with a string-only match on the next added line.
_PATH_OPEN_RE = re.compile(r"^\+\s*path\(\s*$")
_STRING_ONLY_RE = re.compile(r'^\+\s*"([^"]+)"\s*,?\s*$')


@dataclass(frozen=True)
class Finding:
    route: str

    def warning(self) -> str:
        return (
            f"  - new API route '{self.route}' is not mentioned in any .claude/skills/**/SKILL.md"
        )


def _staged_diff() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--", _API_URLS_PATH],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return result.stdout


def _added_routes(diff: str) -> list[str]:
    """Extract URL fragments from added ``path(...)`` entries in the diff.

    Handles two shapes:
    * single-line:  ``+    path("a/b/", view, name="...")``
    * multi-line:   ``+    path(\\n+        "a/b/",\\n+        ...``
    """
    routes: list[str] = []
    expecting_string = False
    for line in diff.splitlines():
        single = _ADDED_PATH_RE.match(line)
        if single:
            routes.append(single.group(1))
            expecting_string = False
            continue
        if _PATH_OPEN_RE.match(line):
            expecting_string = True
            continue
        if expecting_string:
            string_match = _STRING_ONLY_RE.match(line)
            if string_match:
                routes.append(string_match.group(1))
            # Whether or not we matched, the next ``+    "..."`` line was
            # our chance — reset so a stray quoted string later in the
            # block doesn't get treated as a second route.
            expecting_string = False
    return routes


def _skill_texts() -> list[str]:
    if not _SKILLS_DIR.exists():
        return []
    return [
        path.read_text(encoding="utf-8") for path in _SKILLS_DIR.rglob("SKILL.md") if path.is_file()
    ]


def _route_is_documented(route: str, skill_texts: list[str]) -> bool:
    """A route is documented if any skill text contains its URL fragment.

    We compare against the literal route (e.g.
    ``investigations/<uuid:pk>/pdf/``) AND against a normalized form
    where Django path converters become a placeholder. The skills tend
    to write ``investigations/<uuid>/pdf/`` or ``investigations/$INV_ID/pdf/``
    in their curl examples, so either form should count as documented.
    """
    if any(route in text for text in skill_texts):
        return True
    # Strip ``:name`` from each ``<converter:name>`` segment.
    stripped = re.sub(r"<(\w+):\w+>", r"<\1>", route)
    if any(stripped in text for text in skill_texts):
        return True
    # Strip the entire ``<...>`` segment — matches skills that wrote
    # ``investigations/<uuid>/pdf/`` (no name) or used a bash var stand-in.
    no_converter = re.sub(r"<[^>]+>", "<id>", route)
    if any(no_converter in text for text in skill_texts):
        return True
    return False


def main() -> int:
    strict = "--strict" in sys.argv
    diff = _staged_diff()
    if not diff.strip():
        return 0

    routes = _added_routes(diff)
    if not routes:
        return 0

    skill_texts = _skill_texts()
    findings = [Finding(route=r) for r in routes if not _route_is_documented(r, skill_texts)]
    if not findings:
        return 0

    print("[skill-parity-soft-warn] new API routes without a matching skill mention:")
    for f in findings:
        print(f.warning())
    print(
        "  Consider whether the opp-* skills under .claude/skills/ should "
        "document how to call these. Soft warn — commit proceeds."
    )

    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
