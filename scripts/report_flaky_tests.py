"""Report pytest-rerunfailures retries as GitHub Actions warnings.

The CI lanes run pytest with ``--reruns`` so a transient flake can pass on the
second attempt without blocking every merge. This script keeps that visible by
parsing pytest's rerun summary (and JUnit XML when available), then writing
warnings plus a step-summary table.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RERUN_LINE_RE = re.compile(r"^RERUN\s+(.+?)\s*$", re.MULTILINE)
RERUN_XML_TAGS = {"rerun", "flakyFailure", "flakyError"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def collect_from_log(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [match.group(1).strip() for match in RERUN_LINE_RE.finditer(text)]


def collect_from_junit(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []

    tests: list[str] = []
    for case in root.iter():
        if _local_name(case.tag) != "testcase":
            continue
        if not any(_local_name(child.tag) in RERUN_XML_TAGS for child in case):
            continue
        classname = case.attrib.get("classname", "").strip()
        name = case.attrib.get("name", "").strip()
        if classname and name:
            tests.append(f"{classname}::{name}")
        elif name:
            tests.append(name)
    return tests


def unique_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_report(flaky_tests: list[str], *, summary_path: str | None = None) -> int:
    tests = unique_sorted(flaky_tests)
    if not tests:
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write("### Pytest Flake Retries\n\nNo tests required a rerun.\n\n")
        return 0

    for nodeid in tests:
        print(
            "::warning title=Flaky test passed after retry::"
            f"{_github_escape(nodeid)}"
        )

    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("### Pytest Flake Retries\n\n")
            summary.write(
                "These tests passed only after `pytest-rerunfailures` retried them.\n\n"
            )
            summary.write("| Test |\n| --- |\n")
            for nodeid in tests:
                summary.write(f"| `{nodeid}` |\n")
            summary.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="*", type=Path, help="pytest log files to scan")
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        type=Path,
        help="JUnit XML file produced by pytest.",
    )
    args = parser.parse_args(argv)

    flaky: list[str] = []
    for path in args.logs:
        flaky.extend(collect_from_log(path))
    for path in args.junit:
        flaky.extend(collect_from_junit(path))
    return emit_report(flaky, summary_path=os.environ.get("GITHUB_STEP_SUMMARY"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
