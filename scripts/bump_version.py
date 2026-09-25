"""Set the product version everywhere ``check_versions.py`` looks.

Sixteen files carry the release version. Bumping them by hand is how they drift,
and the drift is only caught at release time by a gate that refuses to tag. This
writes all of them from one argument and then re-runs the checker, so a bump is
one reviewable step rather than sixteen.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_versions import PYPROJECTS, PYTHON_VERSION_FILES, ROOT, SEMVER  # noqa: E402


def _sub(path: Path, pattern: str, replacement: str, *, count: int = 1) -> bool:
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, replacement, text, count=count, flags=re.MULTILINE)
    if n == 0:
        raise SystemExit(f"{path}: no match for {pattern!r} — refusing a silent no-op")
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="new version, e.g. 1.0.0")
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"not valid SemVer: {version!r}")

    changed = []

    path = ROOT / "VERSION"
    if path.read_text(encoding="utf-8").strip() != version:
        path.write_text(version + "\n", encoding="utf-8")
        changed.append("VERSION")

    for relative in PYPROJECTS:
        # Only the [project] version, never a dependency pin.
        if _sub(ROOT / relative, r'^version\s*=\s*"[^"]+"', f'version = "{version}"'):
            changed.append(relative)

    for relative in PYTHON_VERSION_FILES:
        if _sub(ROOT / relative, r'^__version__\s*=\s*["\'][^"\']+["\']',
                f'__version__ = "{version}"'):
            changed.append(relative)

    package = ROOT / "apps/web/package.json"
    data = json.loads(package.read_text(encoding="utf-8"))
    if data.get("version") != version:
        # Rewrite in place so formatting and key order survive.
        _sub(package, r'^(\s*"version"\s*:\s*)"[^"]+"', rf'\g<1>"{version}"')
        changed.append("apps/web/package.json")

    chart = ROOT / "deploy/helm/nodyra/Chart.yaml"
    if _sub(chart, r"^version:\s*.+$", f"version: {version}"):
        changed.append("Chart.yaml#version")
    if _sub(chart, r'^appVersion:\s*.+$', f'appVersion: "{version}"'):
        changed.append("Chart.yaml#appVersion")

    values = ROOT / "deploy/helm/nodyra/values.yaml"
    if _sub(values, r'^(\s{2}tag:\s*)["\']?[^\s"\']+["\']?', rf'\g<1>"{version}"'):
        changed.append("values.yaml#image.tag")

    print(f"Wrote {version} to {len(changed)} location(s).")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_versions.py"), "--tag", f"v{version}"]
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
