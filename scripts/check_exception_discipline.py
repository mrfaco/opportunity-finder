"""Flag the patterns ruff can't catch on its own.

We forbid two shapes that ruff doesn't reject by default:

1. **Log and continue** — an ``except`` block that logs and then keeps going
   without re-raising. Ruff's S110/S112 only catch ``pass`` / ``continue``;
   they miss ``logger.error(...)`` followed by silent fall-through.

2. **Fallback-on-error** — an ``except`` block that returns or assigns a
   default value instead of raising. This is the same anti-pattern in a
   different costume.

If an ``except`` block legitimately needs to suppress (rare — e.g. cleanup
that must not mask the original error), annotate with the comment
``# allow: suppress-exception`` on the ``except`` line. The checker honors
that opt-out so we can grep for every instance during review.

Run via pre-commit or directly:

    python scripts/check_exception_discipline.py [paths...]

Exits non-zero on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ALLOW_MARKER = "allow: suppress-exception"

# Directories scanned by default when no args are passed.
DEFAULT_TARGETS = [
    "agents",
    "clusters",
    "config",
    "core",
    "ingestion",
    "investigations",
    "notifications",
]

EXCLUDE_DIR_PARTS = {"migrations", "__pycache__", ".venv", "venv"}


def _file_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _has_allow_marker(lines: list[str], handler: ast.ExceptHandler) -> bool:
    line = lines[handler.lineno - 1]
    return ALLOW_MARKER in line


def _reraises(handler: ast.ExceptHandler) -> bool:
    """True iff every code path in the handler either raises or returns to abort."""

    def walk(nodes: list[ast.stmt]) -> bool:
        for node in nodes:
            if isinstance(node, ast.Raise):
                return True
            # ``return`` after logging is still a "swallow" — the caller never
            # sees the exception. We treat it as a violation, not a re-raise.
            if isinstance(node, ast.If):
                # Conservative: require both branches to re-raise.
                if not walk(node.body):
                    return False
                if node.orelse and not walk(node.orelse):
                    return False
                # If there is no else branch, missing-else means the false path
                # falls through without raising.
                if not node.orelse:
                    return False
                return True
            if isinstance(node, ast.Try):
                # A nested try counts only if its finally re-raises, or the
                # try-body itself unconditionally raises.
                if walk(node.body):
                    return True
        return False

    return walk(handler.body)


def _is_pure_pass(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # Mirror ruff's behavior: surface the syntax error and abort.
        return [f"{path}:{exc.lineno}: syntax error during scan: {exc.msg}"]
    lines = source.splitlines()
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _has_allow_marker(lines, node):
            continue
        if _is_pure_pass(node):
            # Ruff S110 owns this case — don't double-report.
            continue
        if _reraises(node):
            continue
        exc_name = ast.unparse(node.type) if node.type is not None else "<bare>"
        violations.append(
            f"{path}:{node.lineno}: except {exc_name} does not re-raise. "
            "Either `raise` (preserve traceback), `raise NewError from exc` "
            "(translate), or annotate with `# allow: suppress-exception` if "
            "suppressing is genuinely intentional."
        )
    return violations


def _iter_targets(args: list[str]) -> list[Path]:
    if args:
        return [Path(a) for a in args]
    return [Path(d) for d in DEFAULT_TARGETS]


def _iter_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        if t.is_file() and t.suffix == ".py":
            out.append(t)
            continue
        if not t.is_dir():
            continue
        for p in t.rglob("*.py"):
            if any(part in EXCLUDE_DIR_PARTS for part in p.parts):
                continue
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    files = _iter_files(_iter_targets(argv[1:]))
    all_violations: list[str] = []
    for f in files:
        all_violations.extend(_check_file(f))
    if all_violations:
        for v in all_violations:
            print(v, file=sys.stderr)
        print(
            f"\n{len(all_violations)} exception-discipline violation(s). "
            "See AGENTS.md for the rules.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
