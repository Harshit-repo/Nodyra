"""Provision the ML environment and run the two shippable ML example workflows.

Usage:
    set NOODLE_TOKEN=<bearer token>
    python scripts/ml_examples.py

Reads the API at NOODLE_API (default http://localhost:8000).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("NOODLE_API", "http://localhost:8000")
TOKEN = os.environ["NOODLE_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc


# ---------------------------------------------------------------------------
# 1. Ensure the ML environment exists and is ready.
# ---------------------------------------------------------------------------

ML_PACKAGES = ["scikit-learn>=1.4", "pandas>=2.0", "numpy>=1.26", "joblib>=1.3"]


def ensure_ml_env() -> str:
    envs = _req("GET", "/environments")
    for env in envs:
        if env["name"] == "ML / Data Science":
            print(f"  reuse env {env['id']} status={env['status']}")
            return env["id"]
    created = _req(
        "POST",
        "/environments",
        {
            "name": "ML / Data Science",
            "python_version": "3.12",
            "packages": ML_PACKAGES,
            "description": "scikit-learn + pandas + numpy + joblib for the ML nodes.",
        },
    )
    print(f"  created env {created['id']}")
    return created["id"]


def wait_env_ready(env_id: str, timeout: float = 600.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        env = _req("GET", f"/environments/{env_id}")
        if env["status"] != last:
            print(f"  env status -> {env['status']}")
            last = env["status"]
        if env["status"] == "ready":
            return
        if env["status"] == "error":
            raise SystemExit(f"environment build failed: {env.get('build_log', '')[-2000:]}")
        time.sleep(5)
    raise SystemExit("timed out waiting for env to be ready")


# ---------------------------------------------------------------------------
# 2. Graph builders.
# ---------------------------------------------------------------------------

def node(node_id: str, ntype: str, params: dict, x: int, y: int) -> dict:
    return {"id": node_id, "type": ntype, "params": params, "position": {"x": x, "y": y}}


def edge(src: str, tgt: str, source_output: str = "main", target_input: str = "input") -> dict:
    return {
        "id": f"{src}:{source_output}->{tgt}:{target_input}",
        "source": src,
        "source_output": source_output,
        "target": tgt,
        "target_input": target_input,
    }


CLASSIFICATION_CODE = '''
import random
rng = random.Random(42)
rows = []
for _ in range(240):
    # Two well-separated flower species by petal length / width.
    if rng.random() < 0.5:
        rows.append({
            "petal_length": round(rng.gauss(1.5, 0.25), 3),
            "petal_width": round(rng.gauss(0.3, 0.1), 3),
            "species": "setosa",
        })
    else:
        rows.append({
            "petal_length": round(rng.gauss(4.8, 0.4), 3),
            "petal_width": round(rng.gauss(1.6, 0.25), 3),
            "species": "versicolor",
        })
output = rows
'''

REGRESSION_CODE = '''
import random
rng = random.Random(7)
rows = []
for _ in range(240):
    size = round(rng.uniform(50, 250), 1)          # square metres
    bedrooms = rng.randint(1, 5)
    age = round(rng.uniform(0, 40), 1)             # years
    price = 50_000 + 3_000 * size + 25_000 * bedrooms - 1_200 * age
    price += rng.gauss(0, 8000)
    rows.append({
        "size_sqm": size,
        "bedrooms": bedrooms,
        "age_years": age,
        "price": round(price, 2),
    })
output = rows
'''


def classification_graph() -> dict:
    return {
        "nodes": [
            node("trigger", "manual_trigger", {"data": {}}, 80, 200),
            node("generate", "code", {"code": CLASSIFICATION_CODE}, 320, 200),
            node("dataset", "records_to_dataset", {}, 560, 200),
            node(
                "train",
                "train_classifier",
                {
                    "target_column": "species",
                    "feature_columns": "",
                    "algorithm": "random_forest",
                    "test_size": 0.25,
                    "scale": True,
                    "random_state": 42,
                },
                800,
                120,
            ),
            node("save", "save_model", {"name": "iris-species"}, 1040, 120),
            node("predict", "ml_predict", {"output_column": "prediction", "include_proba": True}, 1040, 300),
        ],
        "edges": [
            edge("trigger", "generate"),
            edge("generate", "dataset"),
            edge("dataset", "train"),
            edge("train", "save", source_output="model", target_input="model"),
            edge("train", "predict", source_output="model", target_input="model"),
            edge("dataset", "predict", target_input="data"),
        ],
    }


def regression_graph() -> dict:
    return {
        "nodes": [
            node("trigger", "manual_trigger", {"data": {}}, 80, 200),
            node("generate", "code", {"code": REGRESSION_CODE}, 320, 200),
            node("dataset", "records_to_dataset", {}, 560, 200),
            node(
                "select",
                "select_features",
                {"target_column": "price", "k": 2, "task": "regression"},
                800,
                200,
            ),
            node(
                "train",
                "train_regressor",
                {
                    "target_column": "price",
                    "feature_columns": "",
                    "algorithm": "linear_regression",
                    "test_size": 0.25,
                    "scale": False,
                    "random_state": 7,
                },
                1040,
                120,
            ),
            node("save", "save_model", {"name": "house-prices"}, 1280, 120),
        ],
        "edges": [
            edge("trigger", "generate"),
            edge("generate", "dataset"),
            edge("dataset", "train"),
            edge("train", "save", source_output="model", target_input="model"),
            edge("dataset", "select"),
        ],
    }


# ---------------------------------------------------------------------------
# 3. Workflow create / configure / run.
# ---------------------------------------------------------------------------

def upsert_workflow(name: str, graph: dict, env_id: str) -> str:
    existing = _req("GET", "/workflows?limit=200")
    wf_id = None
    for item in existing.get("items", []):
        if item["name"] == name:
            wf_id = item["id"]
            break
    if wf_id is None:
        wf_id = _req("POST", "/workflows", {"name": name})["id"]
        print(f"  created workflow {wf_id}")
    else:
        print(f"  reuse workflow {wf_id}")
    _req("PUT", f"/workflows/{wf_id}", {"graph": graph, "environment_id": env_id})
    return wf_id


def run_and_wait(wf_id: str, timeout: float = 300.0) -> dict:
    run_id = _req("POST", f"/workflows/{wf_id}/run", {})["run_id"]
    print(f"  run {run_id} started")
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = _req("GET", f"/runs/{run_id}")
        if run["status"] in ("success", "error", "cancelled"):
            return run
        time.sleep(3)
    raise SystemExit("timed out waiting for run")


def node_outputs(run: dict) -> dict:
    out = {}
    for nr in run.get("node_runs", []):
        out[nr.get("node_id")] = {
            "status": nr.get("status"),
            "output": nr.get("output"),
            "error": nr.get("error"),
        }
    return out


def main() -> None:
    print("[1/4] ML environment")
    env_id = ensure_ml_env()
    wait_env_ready(env_id)

    print("[2/4] Classification example")
    clf_id = upsert_workflow("ML Example — Iris Classification", classification_graph(), env_id)
    clf_run = run_and_wait(clf_id)
    print(f"  run status: {clf_run['status']}")
    clf_nodes = node_outputs(clf_run)
    metrics = (clf_nodes.get("train", {}).get("output") or {}).get("metrics")
    if isinstance(metrics, dict):
        print(f"  accuracy={metrics.get('accuracy')} f1={metrics.get('f1')} classes={metrics.get('classes')}")
    print(f"  save -> {clf_nodes.get('save', {}).get('output', {}).get('saved_as') if isinstance(clf_nodes.get('save', {}).get('output'), dict) else clf_nodes.get('save', {}).get('status')}")
    for nid, info in clf_nodes.items():
        if info["status"] != "success":
            print(f"  !! {nid}: {info['status']} {info['error']}")

    print("[3/4] Regression example")
    reg_id = upsert_workflow("ML Example — House Price Regression", regression_graph(), env_id)
    reg_run = run_and_wait(reg_id)
    print(f"  run status: {reg_run['status']}")
    reg_nodes = node_outputs(reg_run)
    rmetrics = (reg_nodes.get("train", {}).get("output") or {}).get("metrics")
    if isinstance(rmetrics, dict):
        print(f"  r2={rmetrics.get('r2')} rmse={rmetrics.get('rmse')} coef={rmetrics.get('coefficients')}")
    for nid, info in reg_nodes.items():
        if info["status"] != "success":
            print(f"  !! {nid}: {info['status']} {info['error']}")

    print("[4/4] Done")
    print(json.dumps({
        "env_id": env_id,
        "classification_workflow_id": clf_id,
        "classification_status": clf_run["status"],
        "regression_workflow_id": reg_id,
        "regression_status": reg_run["status"],
    }, indent=2))


if __name__ == "__main__":
    main()
