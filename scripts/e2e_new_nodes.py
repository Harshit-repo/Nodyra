"""End-to-end check for the new ML-registry / monitor / chart / report nodes.

Run A: train -> register_model -> monitor_model -> metrics_chart -> build_report.
Run B (separate run): load_model -> ml_predict, proving the persistent
registry is readable across runs.

Usage:
    set NOODLE_TOKEN=<bearer token>
    python scripts/e2e_new_nodes.py
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


def node(node_id: str, ntype: str, params: dict, x: int, y: int) -> dict:
    return {"id": node_id, "type": ntype, "params": params, "position": {"x": x, "y": y}}


def edge(src: str, tgt: str, so: str = "main", ti: str = "input") -> dict:
    return {"id": f"{src}:{so}->{tgt}:{ti}", "source": src, "source_output": so,
            "target": tgt, "target_input": ti}


GEN_CODE = '''
import random
rng = random.Random(42)
rows = []
for _ in range(240):
    if rng.random() < 0.5:
        rows.append({"petal_length": round(rng.gauss(1.5, 0.25), 3),
                     "petal_width": round(rng.gauss(0.3, 0.1), 3), "species": "setosa"})
    else:
        rows.append({"petal_length": round(rng.gauss(4.8, 0.4), 3),
                     "petal_width": round(rng.gauss(1.6, 0.25), 3), "species": "versicolor"})
output = rows
'''


def graph_a() -> dict:
    return {
        "nodes": [
            node("trigger", "manual_trigger", {"data": {}}, 80, 200),
            node("generate", "code", {"code": GEN_CODE}, 300, 200),
            node("dataset", "records_to_dataset", {}, 520, 200),
            node("train", "train_classifier",
                 {"target_column": "species", "feature_columns": "",
                  "algorithm": "random_forest", "test_size": 0.25,
                  "scale": False, "random_state": 42}, 740, 120),
            node("register", "register_model", {"name": "e2e-iris"}, 960, 60),
            node("monitor", "monitor_model",
                 {"name": "e2e-iris", "target_column": "species",
                  "metric": "auto", "threshold": ""}, 960, 200),
            node("impchart", "metrics_chart",
                 {"source": "feature_importances", "title": "Importances"}, 960, 340),
            node("report", "build_report", {"title": "E2E report", "columns": 12}, 1200, 200),
        ],
        "edges": [
            edge("trigger", "generate"),
            edge("generate", "dataset"),
            edge("dataset", "train"),
            edge("train", "register", so="model", ti="model"),
            edge("train", "monitor", so="model", ti="model"),
            edge("dataset", "monitor", ti="data"),
            edge("train", "impchart", so="metrics", ti="input"),
            edge("impchart", "report", ti="tile1"),
            edge("train", "report", so="metrics", ti="tile2"),
            edge("dataset", "report", ti="tile3"),
        ],
    }


def graph_b() -> dict:
    return {
        "nodes": [
            node("trigger", "manual_trigger", {"data": {}}, 80, 200),
            node("generate", "code", {"code": GEN_CODE}, 300, 200),
            node("dataset", "records_to_dataset", {}, 520, 200),
            node("load", "load_model", {"name": "e2e-iris"}, 520, 360),
            node("predict", "ml_predict",
                 {"output_column": "prediction", "include_proba": False}, 760, 200),
            node("rows", "dataset_to_records", {"max_rows": 5, "allow_truncate": True}, 980, 200),
        ],
        "edges": [
            edge("trigger", "generate"),
            edge("generate", "dataset"),
            edge("dataset", "predict", ti="data"),
            edge("load", "predict", so="model", ti="model"),
            edge("predict", "rows"),
        ],
    }


def ml_env_id() -> str:
    for env in _req("GET", "/environments"):
        if env["name"] == "ML / Data Science":
            return env["id"]
    raise SystemExit("ML / Data Science env not found")


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
            raise SystemExit(f"env build failed: {str(env.get('status_detail'))[-1500:]}")
        time.sleep(4)
    raise SystemExit("timed out waiting for env")


def upsert(name: str, graph: dict, env_id: str) -> str:
    existing = _req("GET", "/workflows?limit=200")
    wf_id = None
    for item in existing.get("items", []):
        if item["name"] == name:
            wf_id = item["id"]
            break
    if wf_id is None:
        wf_id = _req("POST", "/workflows", {"name": name})["id"]
    _req("PUT", f"/workflows/{wf_id}", {"graph": graph, "environment_id": env_id})
    return wf_id


def run_and_wait(wf_id: str, timeout: float = 300.0) -> dict:
    run_id = _req("POST", f"/workflows/{wf_id}/run", {})["run_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = _req("GET", f"/runs/{run_id}")
        if run["status"] in ("success", "error", "cancelled"):
            return run
        time.sleep(3)
    raise SystemExit("timed out waiting for run")


def outputs(run: dict) -> dict:
    return {nr.get("node_id"): {"status": nr.get("status"), "output": nr.get("output"),
            "error": nr.get("error")} for nr in run.get("node_runs", [])}


def main() -> None:
    env_id = ml_env_id()
    print("[env]", env_id)
    wait_env_ready(env_id)

    print("[A] train -> register -> monitor -> chart -> report")
    a_id = upsert("E2E — Register/Monitor/Report", graph_a(), env_id)
    a = run_and_wait(a_id)
    print("  run status:", a["status"])
    ao = outputs(a)
    for nid, info in ao.items():
        flag = "" if info["status"] == "success" else f"  !! {info['error']}"
        print(f"  {nid}: {info['status']}{flag}")
    reg = ao.get("register", {}).get("output") or {}
    mon = ao.get("monitor", {}).get("output") or {}
    chart = ao.get("impchart", {}).get("output") or {}
    report = ao.get("report", {}).get("output") or {}
    print("  registered_as:", reg.get("registered_as"))
    print("  monitor metric:", (mon.get("metrics") or {}).get("metric"),
          "value:", (mon.get("metrics") or {}).get("value"))
    print("  chart marker:", chart.get("__noodle_chart__"), "series:", len(chart.get("series") or []))
    print("  report marker:", report.get("__noodle_report__"),
          "tiles:", [t.get("type") for t in (report.get("tiles") or [])])

    print("[B] load_model -> predict (cross-run registry)")
    b_id = upsert("E2E — Load/Predict", graph_b(), env_id)
    b = run_and_wait(b_id)
    print("  run status:", b["status"])
    bo = outputs(b)
    for nid, info in bo.items():
        flag = "" if info["status"] == "success" else f"  !! {info['error']}"
        print(f"  {nid}: {info['status']}{flag}")
    rows = bo.get("rows", {}).get("output")
    if isinstance(rows, list) and rows:
        print("  sample prediction:", rows[0].get("prediction"))

    ok = a["status"] == "success" and b["status"] == "success"
    print("\nRESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
