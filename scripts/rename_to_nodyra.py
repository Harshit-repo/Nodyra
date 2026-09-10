"""One-shot case-preserving sweep: noodle -> nodyra in tracked text files.

Skips historical records and generated lock files. Idempotent. This rewrites
file contents only; package directory moves are done with git mv.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

EXEMPT_PREFIXES = (
    "scripts/check_rename.py",
    "scripts/rename_to_nodyra.py",
    "CHANGELOG.md",
    "uv.lock",
    "package-lock.json",
)
EXEMPT_FILES = {
    "docs/upgrading-to-nodyra.md",
}
REPLACEMENTS = (("noodle", "nodyra"), ("Noodle", "Nodyra"), ("NOODLE", "NODYRA"))
EXEMPT_LINE_MARKERS = (
    "previously developed under the working name",
    "NOODLE_",
    "noodle_token",
)


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def _read_utf8_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _replace_text(text: str) -> str:
    lines = text.splitlines(keepends=True)
    rewritten: list[str] = []
    for line in lines:
        if any(marker in line for marker in EXEMPT_LINE_MARKERS):
            rewritten.append(line)
            continue
        new_line = line
        for old, repl in REPLACEMENTS:
            new_line = new_line.replace(old, repl)
        rewritten.append(new_line)
    return "".join(rewritten)


def main() -> None:
    changed = 0
    for rel in _tracked_files():
        if rel.startswith(EXEMPT_PREFIXES) or rel in EXEMPT_FILES:
            continue
        path = Path(rel)
        text = _read_utf8_text(path)
        if text is None:
            continue
        new = _replace_text(text)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
