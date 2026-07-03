"""One-shot case-preserving sweep: noodle -> nodyra in tracked text files.

Skips historical records and generated lock files. Idempotent. This rewrites
file contents only; package directory moves are done with git mv.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

EXEMPT_PREFIXES = (
    "docs/audits/",
    "docs/superpowers/",
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
    "uv.lock",
    "package-lock.json",
)
TEXT_SUFFIXES = {
    ".cfg",
    ".css",
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
REPLACEMENTS = (("noodle", "nodyra"), ("Noodle", "Nodyra"), ("NOODLE", "NODYRA"))


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def main() -> None:
    changed = 0
    for rel in _tracked_files():
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        path = Path(rel)
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "Dockerfile":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = text
        for old, repl in REPLACEMENTS:
            new = new.replace(old, repl)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
