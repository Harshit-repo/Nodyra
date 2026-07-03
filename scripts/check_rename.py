"""Rename gate: fail if tracked files still say 'noodle' outside exemptions.

Historical records and the rename scripts are exempt. Run:
python scripts/check_rename.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EXEMPT_PREFIXES = (
    "docs/audits/",
    "docs/superpowers/",
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
)
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".dockerfile",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".mako",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
PATTERN = re.compile(r"noodle", re.IGNORECASE)
EXEMPT_LINES = {
    # Former-name SEO/support note intentionally retained after the rename.
    ("README.md", "previously developed under the working name"),
}


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def _is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name == "Dockerfile"


def _line_exempt(rel: str, line: str) -> bool:
    lower = line.lower()
    return any(rel == path and marker.lower() in lower for path, marker in EXEMPT_LINES)


def _print(text: str) -> None:
    """Print safely on Windows consoles whose default encoding is not UTF-8."""
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace"))
        sys.stdout.buffer.write(b"\n")


def main() -> int:
    hits: list[str] = []
    for rel in _tracked_files():
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        path = Path(rel)
        if not _is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
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
