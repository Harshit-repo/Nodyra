"""Helpers for comparing pip requirement strings by canonical project name.

Used by the editor (missing-package banner), the run preflight, and the env
package-usage scan so they all agree on what "the env has this package" means.
"""

from __future__ import annotations

import re

# Matches the project-name prefix of a requirement specifier, e.g. the
# "scikit-learn" in "scikit-learn[extra]>=1.0 ; python_version>'3.8'".
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def canonical_package_name(spec: str) -> str:
    """Return the PEP 503 canonical project name from a requirement specifier.

    Strips environment markers, extras, and version constraints, then lowercases
    and collapses runs of ``-``/``_``/``.`` to a single ``-``.
    """
    head = spec.strip().split(";", 1)[0].strip()
    match = _NAME_RE.match(head)
    name = match.group(0) if match else head
    return re.sub(r"[-_.]+", "-", name).lower()


def missing_packages(required: list[str], installed: list[str]) -> list[str]:
    """Return the requirement specifiers in ``required`` whose canonical name is
    not present in ``installed`` (comparison ignores versions/extras/case)."""
    have = {canonical_package_name(p) for p in installed if p.strip()}
    return [req for req in required if canonical_package_name(req) not in have]
