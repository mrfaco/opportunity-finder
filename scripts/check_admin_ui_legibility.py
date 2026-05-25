"""Soft linter for admin templates — catches the dark-mode-legibility traps
that have bitten us repeatedly during the Unfold migration.

The root cause of every "white background again" episode is the same: Unfold
ships a fixed Tailwind CSS bundle that includes only the utility classes its
own templates use. Any class we write that isn't in that bundle silently
falls back to no styling, which on a dark page renders as the browser's
default white background.

This script scans every admin ``*.html`` template for the four shapes that
have actually broken in this repo:

1. ``slate-*`` color classes — Unfold uses ``base-*`` (variable-driven).
   ``bg-slate-900`` is not in the bundle and falls back to white.
2. Opacity modifiers like ``dark:bg-base-800/50`` — Tailwind's ``/<n>``
   variants are JIT-generated and not in Unfold's pre-built bundle.
3. ``prose``, ``prose-invert``, ``prose-sm`` — the Tailwind typography
   plugin isn't bundled; ``prose`` is a no-op and the text renders at the
   browser's default sizes against whatever background remained.
4. ``bg-white`` without a paired ``dark:bg-...`` — light mode bleeding
   into dark mode. Either pair it or use inline styles.

Soft by default: prints warnings, exits 0 so commits go through. Pass
``--strict`` to exit non-zero on any finding (useful in CI).

Run directly:

    python scripts/check_admin_ui_legibility.py [paths...]

Or let pre-commit run it on every commit that touches templates.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Directories scanned when no paths are passed. Same shape as the exception
# discipline checker — every app that owns admin templates.
DEFAULT_TARGETS = [
    "agents/templates",
    "clusters/templates",
    "core/templates",
    "ideation/templates",
    "ingestion/templates",
    "investigations/templates",
]

# Suppress on a single line with: <!-- ui-legibility: ok -->
ALLOW_MARKER = "ui-legibility: ok"


@dataclass(frozen=True)
class Finding:
    path: Path
    lineno: int
    rule: str
    snippet: str
    hint: str


# ---------------------------------------------------------------------------
# Pattern detectors. Each returns a ``Finding`` or ``None`` for a given line.
# Kept as small functions rather than a single mega-regex so the hint text
# can be specific to what was matched (devs get a useful fix-it line, not a
# generic "your CSS is bad").
# ---------------------------------------------------------------------------

# Match slate-* color utilities. Matches inside class="..." (handled by the
# caller — we don't bother re-parsing attributes here, the rule is "anywhere
# in the file" which is good enough for a soft lint).
_SLATE_RE = re.compile(
    r"\b(?:hover:|dark:)?(?:bg|text|border|divide|ring|fill|stroke|from|to|via)-slate-\d+(?:/\d+)?\b"
)

# Tailwind opacity modifier on dark: classes, e.g. ``dark:bg-base-800/50``.
# Unfold's pre-built CSS doesn't include the slash-N variants because they're
# JIT-generated on demand. We allow any color word followed by ``-<digit>/<digit>``.
_OPACITY_MOD_RE = re.compile(r"\bdark:(?:bg|text|border|divide|ring)-[a-z]+-\d+/\d+\b")

# Tailwind typography plugin classes. Unfold doesn't ship @tailwindcss/typography.
_PROSE_RE = re.compile(r"\bprose(?:-invert|-sm|-lg|-xl|-2xl)?\b")

# Bare bg-white in a class attribute — looks for class="..." containing
# bg-white but NOT a paired dark:bg-base-*/dark:bg-white/dark:bg-slate-*.
# This is a coarser check than the others; if a class string is split across
# lines it'll miss the pair, but that's a Tailwind anti-pattern anyway.
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')


def _scan_line(line: str) -> list[tuple[str, str, str]]:
    """Return a list of (rule, matched_snippet, hint) for this line.

    A line can produce multiple findings (e.g. both a ``slate-*`` and a
    ``prose`` violation), but we cap at one per rule to keep output sane.
    """
    findings: list[tuple[str, str, str]] = []

    if (m := _SLATE_RE.search(line)) is not None:
        findings.append(
            (
                "slate-color-class",
                m.group(0),
                "Use base-* instead — Unfold ships base-* in its CSS bundle, not slate-*.",
            )
        )

    if (m := _OPACITY_MOD_RE.search(line)) is not None:
        findings.append(
            (
                "tailwind-opacity-modifier",
                m.group(0),
                "Drop the /<N> opacity modifier — Unfold's pre-built CSS doesn't include "
                "JIT-generated opacity variants. Use a base color, or inline style with rgba().",
            )
        )

    if (m := _PROSE_RE.search(line)) is not None:
        findings.append(
            (
                "tailwind-typography-class",
                m.group(0),
                "Tailwind's @tailwindcss/typography plugin isn't bundled — `prose` does nothing. "
                "Use explicit text-base + leading-relaxed + text-base-100 + space-y-* instead.",
            )
        )

    # bg-white without paired dark: variant inside the SAME class attribute.
    # Don't fire if the attribute also includes some dark: bg fallback.
    for class_match in _CLASS_ATTR_RE.finditer(line):
        classes = class_match.group(1)
        if "bg-white" in classes and not re.search(r"dark:bg-[a-z]+-\d+\b", classes):
            findings.append(
                (
                    "bg-white-no-dark-pair",
                    "bg-white",
                    "Pair with dark:bg-base-900 (or similar), or use inline style. Otherwise "
                    "the element is white in dark mode.",
                )
            )
            break  # one finding per line is enough — don't drown the user

    return findings


def scan_file(path: Path) -> list[Finding]:
    """Scan a single .html file and return all findings."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # Binary file or unreadable — silently skip. The hook should never
        # block on its own infrastructure.
        return []

    findings: list[Finding] = []
    for lineno, line in enumerate(lines, start=1):
        if ALLOW_MARKER in line:
            continue
        for rule, snippet, hint in _scan_line(line):
            findings.append(
                Finding(path=path, lineno=lineno, rule=rule, snippet=snippet, hint=hint)
            )
    return findings


def collect_html_files(roots: list[Path]) -> list[Path]:
    """Recursively find *.html files under the given roots.

    Skips ``unfold/``, ``django/``, and any path containing ``venv`` —
    third-party templates are out of our control and would generate
    constant false positives.
    """
    found: list[Path] = []
    skip_segments = {"venv", "site-packages", "node_modules", ".git"}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".html":
            found.append(root)
            continue
        for p in root.rglob("*.html"):
            if any(seg in skip_segments for seg in p.parts):
                continue
            found.append(p)
    return found


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    strict = False
    if "--strict" in args:
        strict = True
        args.remove("--strict")

    targets = [Path(a) for a in args] if args else [Path(t) for t in DEFAULT_TARGETS]
    files = collect_html_files(targets)
    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(scan_file(f))

    if not all_findings:
        return 0

    # Group by file for readable output.
    by_file: dict[Path, list[Finding]] = {}
    for f in all_findings:
        by_file.setdefault(f.path, []).append(f)

    label = "ERROR" if strict else "warn"
    print(
        f"\n[admin-ui-legibility] {label}: {len(all_findings)} legibility risks in "
        f"{len(by_file)} file{'s' if len(by_file) != 1 else ''}.\n",
        file=sys.stderr,
    )

    for path, items in sorted(by_file.items()):
        print(f"  {path}", file=sys.stderr)
        for item in items:
            print(
                f"    L{item.lineno}  [{item.rule}]  {item.snippet!r}\n"
                f"           ↳ {item.hint}",
                file=sys.stderr,
            )
        print(file=sys.stderr)

    print(
        "  (suppress a specific line with the comment "
        f"`<!-- {ALLOW_MARKER} -->`; "
        "pass --strict to exit non-zero on findings)\n",
        file=sys.stderr,
    )

    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
