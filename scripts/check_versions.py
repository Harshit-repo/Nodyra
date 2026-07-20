"""Fail when product release versions drift across packages and deployment assets."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECTS = [
    "pyproject.toml",
    "apps/api/pyproject.toml",
    "packages/core/pyproject.toml",
    "packages/nodes/pyproject.toml",
    "packages/runtime/pyproject.toml",
    "packages/exporter/pyproject.toml",
    "packages/importer/pyproject.toml",
    "packages/runner/pyproject.toml",
]
PYTHON_VERSION_FILES = [
    "packages/core/nodyra/__init__.py",
    "packages/nodes/nodyra_nodes/__init__.py",
    "packages/runtime/nodyra_runtime/__init__.py",
    "packages/exporter/nodyra_exporter/__init__.py",
    "packages/runner/nodyra_runner_agent/__init__.py",
]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _python_version(path: Path) -> str | None:
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', path.read_text(), re.MULTILINE)
    return match.group(1) if match else None


def collect_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for relative in PYPROJECTS:
        with (ROOT / relative).open("rb") as handle:
            versions[relative] = tomllib.load(handle)["project"]["version"]
    for relative in PYTHON_VERSION_FILES:
        versions[relative] = _python_version(ROOT / relative)
    versions["apps/web/package.json"] = json.loads(
        (ROOT / "apps/web/package.json").read_text()
    )["version"]

    chart = (ROOT / "deploy/helm/nodyra/Chart.yaml").read_text()
    chart_version = re.search(r"^version:\s*([^\s]+)$", chart, re.MULTILINE)
    app_version = re.search(r'^appVersion:\s*["\']?([^\s"\']+)', chart, re.MULTILINE)
    versions["deploy/helm/nodyra/Chart.yaml#version"] = chart_version.group(1) if chart_version else None
    versions["deploy/helm/nodyra/Chart.yaml#appVersion"] = app_version.group(1) if app_version else None
    values = (ROOT / "deploy/helm/nodyra/values.yaml").read_text()
    image_tag = re.search(r'^\s{2}tag:\s*["\']?([^\s"\']+)', values, re.MULTILINE)
    versions["deploy/helm/nodyra/values.yaml#image.tag"] = image_tag.group(1) if image_tag else None
    return versions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Optional git tag, for example v0.1.0")
    args = parser.parse_args()
    expected = (ROOT / "VERSION").read_text().strip()
    errors: list[str] = []
    if not SEMVER.fullmatch(expected):
        errors.append(f"VERSION is not valid SemVer: {expected!r}")
    for location, actual in collect_versions().items():
        if actual != expected:
            errors.append(f"{location}: expected {expected!r}, found {actual!r}")
    if args.tag and args.tag.removeprefix("v") != expected:
        errors.append(f"tag {args.tag!r} does not match VERSION {expected!r}")
    if errors:
        print("Version contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Product version contract is aligned at {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
