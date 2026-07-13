from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_demo_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "proof_point_demos.py"
    spec = importlib.util.spec_from_file_location("proof_point_demos", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_python_native_graph_exposes_code_node_for_single_node_demo() -> None:
    demos = _load_demo_module()

    nodes = {node["id"]: node for node in demos.PYTHON_NATIVE_GRAPH["nodes"]}
    assert nodes["trigger"]["type"] == "manual_trigger"
    assert nodes["transform"]["type"] == "code"
    assert nodes["transform"]["params"]["code"] == "output = input['n'] + 1"

    edge = demos.PYTHON_NATIVE_GRAPH["edges"][0]
    assert edge["source"] == "trigger"
    assert edge["target"] == "transform"
