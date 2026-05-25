"""Soft linter — flag new admin actions and Celery tasks that may need an
API counterpart.

The opportunity-finder HTTP API (``api/views.py``) mirrors the admin's
operator buttons + the Celery task triggers. When a contributor adds a
new ``@shared_task`` or a new entry in ``actions = [...]`` inside an
admin class, there's a decent chance the API surface should grow too —
otherwise the only way to drive the new operation is by clicking through
admin. This linter spots those additions and asks the question.

What it detects:

* Lines added (in the **staged** diff) of the form ``@shared_task`` —
  these are Celery entry points.
* Lines added that match ``actions = [...]`` — Django admin's hook for
  list-action methods, which are exactly the buttons the API mirrors.

For each hit, it greps ``api/views.py`` and ``api/urls.py`` for the
referenced symbol. If the symbol is already wired, the addition is
silent. If not, it prints a one-line warning naming the file + symbol.

Soft by default: prints warnings, exits 0. Pass ``--strict`` to exit
non-zero (useful from CI).

Run directly:

    python scripts/check_api_parity.py            # scan staged diff
    python scripts/check_api_parity.py --strict   # fail if any finding
    python scripts/check_api_parity.py path1 path2  # scan files directly
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_FILES = (_REPO_ROOT / "api" / "views.py", _REPO_ROOT / "api" / "urls.py")

# Match ``@shared_task`` lines and capture the function name on the
# subsequent ``def`` line. The diff scan does this two-line look-ahead;
# the file scan finds them in source directly.
_SHARED_TASK_RE = re.compile(r"^\+\s*@shared_task")
_DEF_RE = re.compile(r"^\+\s*def\s+(\w+)\s*\(")

# Match ``actions = [...]`` declarations (may span multiple lines). We
# extract method names from the list contents.
_ACTIONS_BLOCK_RE = re.compile(r"actions\s*=\s*\[(?P<body>[^\]]*)\]", re.MULTILINE | re.DOTALL)
_QUOTED_NAME_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


@dataclass(frozen=True)
class Finding:
    kind: str  # "task" | "action"
    file: str
    symbol: str

    def warning(self) -> str:
        return (
            f"  - {self.file}: new {self.kind} '{self.symbol}' "
            f"— no reference found in api/views.py or api/urls.py"
        )


def _staged_diff() -> str:
    """Return the staged unified diff (or empty string if not in a git repo)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # No git or no staged changes — silently no-op.
        return ""
    return result.stdout


def _current_file(diff_header: str) -> str | None:
    """Extract the +++ path from a diff section header."""
    match = re.match(r"^\+\+\+ b/(.+)$", diff_header)
    return match.group(1) if match else None


def _api_text() -> str:
    """Return concatenated text of api/views.py + api/urls.py (or empty)."""
    parts = []
    for path in _API_FILES:
        if path.exists():
            parts.append(path.read_text())
    return "\n".join(parts)


def _scan_diff_for_tasks(diff: str) -> list[Finding]:
    """Find ``@shared_task`` additions followed by a ``def name(...)`` line."""
    findings: list[Finding] = []
    current_file: str | None = None
    awaiting_def = False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current_file = _current_file(line)
            awaiting_def = False
            continue
        if line.startswith("---"):
            awaiting_def = False
            continue
        if _SHARED_TASK_RE.match(line):
            awaiting_def = True
            continue
        if awaiting_def and current_file and current_file.endswith(".py"):
            def_match = _DEF_RE.match(line)
            if def_match:
                findings.append(Finding(kind="task", file=current_file, symbol=def_match.group(1)))
                awaiting_def = False
                continue
            # Lines between @shared_task and def are tolerated (decorators,
            # blank lines). Reset only on the next @shared_task or section.
    return findings


def _scan_files_for_actions(files: list[Path]) -> list[Finding]:
    """For each given .py file, parse ``actions = [...]`` blocks."""
    findings: list[Finding] = []
    for path in files:
        if not path.exists() or path.suffix != ".py":
            continue
        text = path.read_text()
        for match in _ACTIONS_BLOCK_RE.finditer(text):
            for name_match in _QUOTED_NAME_RE.finditer(match.group("body")):
                findings.append(
                    Finding(
                        kind="action",
                        file=str(path.relative_to(_REPO_ROOT)),
                        symbol=name_match.group(1),
                    )
                )
    return findings


def _filter_known(findings: list[Finding], api_text: str) -> list[Finding]:
    """Drop findings whose symbol already appears in api/views.py or api/urls.py."""
    return [f for f in findings if f.symbol not in api_text]


def _diff_changed_files(diff: str) -> list[Path]:
    """Parse the diff and return the set of changed file paths under the repo."""
    files: list[Path] = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = _current_file(line)
            if path:
                files.append(_REPO_ROOT / path)
    return files


def main() -> int:
    strict = "--strict" in sys.argv
    direct_paths = [a for a in sys.argv[1:] if not a.startswith("--")]

    if direct_paths:
        # Called with explicit paths — scan them directly. Used in CI / from
        # the command line. No diff parsing in this branch.
        files = [Path(p).resolve() for p in direct_paths]
        action_findings = _scan_files_for_actions(files)
        task_findings = []  # tasks need diff context (we want only *new* ones)
    else:
        diff = _staged_diff()
        if not diff.strip():
            return 0
        changed = _diff_changed_files(diff)
        # Only scan admin.py files for actions (where they live by convention).
        admin_files = [p for p in changed if p.name == "admin.py"]
        action_findings = _scan_files_for_actions(admin_files)
        task_findings = _scan_diff_for_tasks(diff)

    findings = _filter_known(action_findings + task_findings, _api_text())
    if not findings:
        return 0

    print("[api-parity-soft-warn] new admin actions or Celery tasks without API hooks:")
    for f in findings:
        print(f.warning())
    print(
        "  Consider whether these should be reachable via the API "
        "(api/views.py, api/urls.py). Soft warn — commit proceeds."
    )

    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
