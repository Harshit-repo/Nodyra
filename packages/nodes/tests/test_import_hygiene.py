"""Regression guard: importing ``nodyra_nodes`` must never pull in heavy
third-party libraries at module scope.

Warm pool scale-up (a new ``_RuntimeProcess`` spawned by
``app.services.runtime_pool``) and sandboxed per-run containers both pay
``import nodyra_nodes`` on every process spawn — ``nodyra_nodes/__init__.py``
imports ~55 node modules eagerly. If any of those modules import pandas,
numpy, boto3, openpyxl, matplotlib, sklearn, or PIL at module scope, every
cold start pays that import cost even for workflows that never touch the
node that needs it. Node modules keep such imports at function scope
(inside the node's ``run``/handler) instead.

Run in a subprocess so this test process's own imports (pytest plugins,
other already-imported test modules) can't mask a real regression — a
subprocess starts with a clean ``sys.modules``.
"""

import json
import subprocess
import sys

HEAVY = ["pandas", "numpy", "boto3", "openpyxl", "matplotlib", "sklearn", "PIL"]


def test_importing_nodyra_nodes_does_not_load_heavy_libraries() -> None:
    code = (
        "import sys, json, nodyra_nodes; "
        f"print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"importing nodyra_nodes failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert loaded == [], (
        f"importing nodyra_nodes eagerly loaded heavy modules: {loaded}. "
        "Move the offending top-level import to function scope in the node "
        "module that needs it."
    )
