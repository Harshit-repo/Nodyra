"""Rename gate: fail if tracked files still say 'noodle' outside exemptions.

Historical records and the rename scripts are exempt. Run:
python scripts/check_rename.py
"""

from __future__ import annotations

import re
import subprocess
import sys

EXEMPT_PREFIXES = (
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
)
EXEMPT_FILES = {
    "docs/upgrading-to-nodyra.md",
}
PATTERN = re.compile(r"noodle", re.IGNORECASE)
EXEMPT_LINES = {
    # Former-name SEO/support note intentionally retained after the rename.
    ("README.md", "previously developed under the name"),
}
EXEMPT_LINE_PATTERNS = (
    re.compile(r"NOODLE_[A-Z0-9_]+"),
    re.compile(r"noodle_token"),
    # Canonical repository links keep the historical GitHub slug. Renaming the
    # remote would break releases, runbooks, and existing clones.
    re.compile(r"https://github\.com/Harshit-repo/noodle(?:[/#?]|\b)", re.IGNORECASE),
)


def _grep_hits() -> list[tuple[str, str, str]]:
    result = subprocess.run(
        ["git", "grep", "-n", "-I", "-i", "noodle", "--", "."],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        check=False,
    )
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git grep failed")
    hits: list[tuple[str, str, str]] = []
    for raw in result.stdout.splitlines():
        rel, line_no, line = raw.split(":", 2)
        hits.append((rel.strip('"'), line_no, line))
    return hits


def _line_exempt(rel: str, line: str) -> bool:
    lower = line.lower()
    return any(rel == path and marker.lower() in lower for path, marker in EXEMPT_LINES) or any(
        pattern.search(line) for pattern in EXEMPT_LINE_PATTERNS
    )


def _print(text: str) -> None:
    """Print safely on Windows consoles whose default encoding is not UTF-8."""
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(
            text.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace")
        )
        sys.stdout.buffer.write(b"\n")


def main() -> int:
    hits: list[str] = []
    for rel, line_no, line in _grep_hits():
        if rel.startswith(EXEMPT_PREFIXES) or rel in EXEMPT_FILES:
            continue
        if PATTERN.search(line) and not _line_exempt(rel, line):
            hits.append(f"{rel}:{line_no}: {line.strip()[:120]}")
    if hits:
        _print(f"{len(hits)} 'noodle' occurrence(s) remain:")
        _print("\n".join(hits[:200]))
        return 1
    _print("clean: no 'noodle' outside exempt paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
