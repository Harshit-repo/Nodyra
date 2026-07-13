"""End-to-end app tester — drives the REST API exactly as the web client does.

Run:  python scripts/e2e_test.py
Reads the auth token from NODYRA_TOKEN env var (extracted from the browser).
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any

import requests

BASE = os.environ.get("NODYRA_API", "http://localhost:8000")
TOKEN = os.environ["NODYRA_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ISSUES: list[str] = []
PASSED: list[str] = []


def _configure_output() -> None:
    """Keep Unicode status markers readable on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def issue(msg: str) -> None:
    ISSUES.append(msg)
    print(f"  ❌ ISSUE: {msg}")


def ok(msg: str) -> None:
    PASSED.append(msg)
    print(f"  ✅ {msg}")


def pos(i: int, j: int = 0) -> dict[str, float]:
    return {"x": 120.0 + i * 240.0, "y": 120.0 + j * 160.0}


def create_workflow(name: str) -> str:
    r = requests.post(f"{BASE}/workflows", headers=H, json={"name": name})
    r.raise_for_status()
    return r.json()["id"]


def set_graph(wf_id: str, nodes: list[dict], edges: list[dict]) -> dict:
    r = requests.put(
        f"{BASE}/workflows/{wf_id}",
        headers=H,
        json={"graph": {"nodes": nodes, "edges": edges}},
    )
    if r.status_code >= 400:
        return {"_error": f"{r.status_code} {r.text}"}
    return r.json()


def run_and_wait(wf_id: str, data: dict | None = None, timeout: float = 60.0) -> dict:
    body: dict[str, Any] = {"mode": "manual"}
    if data is not None:
        body["data"] = data
    r = requests.post(f"{BASE}/workflows/{wf_id}/run", headers=H, json=body)
    if r.status_code >= 400:
        return {"status": "dispatch_error", "detail": r.text, "code": r.status_code}
    run_id = r.json()["run_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        rr = requests.get(f"{BASE}/runs/{run_id}", headers=H)
        rr.raise_for_status()
        info = rr.json()
        if info["status"] in ("success", "error", "canceled"):
            return info
        time.sleep(0.6)
    return {"status": "timeout", "run_id": run_id}


def node_out(run: dict, node_id: str) -> Any:
    for nr in run.get("node_runs", []):
        if nr["node_id"] == node_id:
            return nr
    return None


def main_value(nr: dict | None) -> Any:
    """Unwrap a node run's primary output port ('main')."""
    if not nr:
        return None
    out = nr.get("output")
    if isinstance(out, dict) and "main" in out:
        return out["main"]
    return out


def summarize(run: dict) -> str:
    parts = []
    for nr in run.get("node_runs", []):
        s = nr["status"]
        mark = "ok" if s == "success" else s.upper()
        err = f" err={nr['error'][:120]}" if nr.get("error") else ""
        parts.append(f"{nr['node_id']}={mark}{err}")
    return "; ".join(parts)


def n(node_id: str, ntype: str, i: int, j: int = 0, **params) -> dict:
    return {"id": node_id, "type": ntype, "params": params, "position": pos(i, j)}


def e(src: str, tgt: str, source_output: str = "main", target_input: str = "input") -> dict:
    return {
        "source": src,
        "target": tgt,
        "source_output": source_output,
        "target_input": target_input,
    }


# ---------------------------------------------------------------------------
TESTS: list[tuple[str, Any]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def t01_basic_linear():
    """Manual trigger -> edit_fields -> code passthrough."""
    wf = create_workflow(f"E2E-basic-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0, params={}),
        n("edit", "edit_fields", 1, fields={"greeting": "hello"}),
        n("code", "code", 2, code="output = {'ok': True, 'echo': input}"),
    ]
    edges = [e("trig", "edit"), e("edit", "code")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t01 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf, data={"name": "Ada"})
    print(f"  run: {run['status']} | {summarize(run)}")
    if run["status"] != "success":
        issue(f"t01 basic linear run not success: {run['status']} :: {summarize(run)}")
    else:
        ok("t01 basic linear workflow ran")


@test
def t02_if_branch():
    """If node routing true/false."""
    wf = create_workflow(f"E2E-if-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0),
        n("if", "if", 1, field="age", operator="greater than", value="18"),
        n("adult", "no_op", 2, 0),
        n("minor", "no_op", 2, 1),
    ]
    edges = [
        e("trig", "if"),
        e("if", "adult", source_output="true"),
        e("if", "minor", source_output="false"),
    ]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t02 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf, data={"age": 25})
    print(f"  run: {run['status']} | {summarize(run)}")
    if run["status"] != "success":
        issue(f"t02 if-branch run not success: {summarize(run)}")
    else:
        ok("t02 if-branch workflow ran")


@test
def t03_dataset_pipeline():
    """records_to_dataset -> dataset_filter -> dataset_to_records."""
    wf = create_workflow(f"E2E-dataset-{uuid.uuid4().hex[:6]}")
    code = (
        "output = [{'id': i, 'score': i * 10} for i in range(1, 11)]"
    )
    nodes = [
        n("trig", "manual_trigger", 0),
        n("gen", "code", 1, code=code),
        n("ds", "records_to_dataset", 2),
        n("filt", "dataset_filter", 3, where="score > 50"),
        n("recs", "dataset_to_records", 4, max_rows=100),
    ]
    edges = [
        e("trig", "gen"),
        e("gen", "ds"),
        e("ds", "filt"),
        e("filt", "recs"),
    ]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t03 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    rows = main_value(node_out(run, "recs"))
    if run["status"] != "success":
        issue(f"t03 dataset pipeline not success: {summarize(run)}")
    elif isinstance(rows, list):
        cnt = len(rows)
        if cnt == 5:
            ok(f"t03 dataset filter pipeline produced {cnt} rows (score>50)")
        else:
            issue(f"t03 dataset filter expected 5 rows, got {cnt}")
    else:
        issue(f"t03 dataset_to_records output not a list: {rows}")


@test
def t04_datasetref_auto_expand_loop():
    """NEW FEATURE: DatasetRef wired into loop_over_items must expand to rows."""
    wf = create_workflow(f"E2E-dsexpand-{uuid.uuid4().hex[:6]}")
    code = "output = [{'id': i} for i in range(1, 6)]"
    nodes = [
        n("trig", "manual_trigger", 0),
        n("gen", "code", 1, code=code),
        n("ds", "records_to_dataset", 2),
        n("loop", "loop_over_items", 3),
        n("each", "no_op", 4),
    ]
    edges = [
        e("trig", "gen"),
        e("gen", "ds"),
        e("ds", "loop"),
        e("loop", "each", source_output="item"),
    ]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t04 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    each = main_value(node_out(run, "each"))
    if run["status"] != "success":
        issue(f"t04 datasetref->loop run not success: {summarize(run)}")
    elif isinstance(each, list) and len(each) == 5:
        ok("t04 DatasetRef auto-expanded into 5 loop items (new feature works)")
    else:
        issue(f"t04 DatasetRef NOT expanded into rows; loop 'each' output={each!r}")


@test
def t05_ml_data_prep():
    """ML-style: synthesize features, aggregate stats via dataset + duckdb_sql."""
    wf = create_workflow(f"E2E-ml-prep-{uuid.uuid4().hex[:6]}")
    code = (
        "import random\n"
        "random.seed(42)\n"
        "rows = []\n"
        "for i in range(200):\n"
        "    x = random.random()\n"
        "    rows.append({'feature': round(x, 4), 'label': 1 if x > 0.5 else 0})\n"
        "output = rows"
    )
    nodes = [
        n("trig", "manual_trigger", 0),
        n("gen", "code", 1, code=code),
        n("ds", "records_to_dataset", 2),
        n("sql", "duckdb_sql", 3,
          sql=("SELECT label, COUNT(*) AS n, AVG(feature) AS mean_feat "
               "FROM input GROUP BY label ORDER BY label")),
        n("recs", "dataset_to_records", 4, max_rows=10),
    ]
    edges = [e("trig", "gen"), e("gen", "ds"), e("ds", "sql"), e("sql", "recs")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t05 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    rows = main_value(node_out(run, "recs"))
    if run["status"] != "success":
        issue(f"t05 ML data-prep run not success: {summarize(run)}")
    elif isinstance(rows, list) and len(rows) == 2:
        ok(f"t05 ML aggregation produced class stats: {rows}")
    else:
        issue(f"t05 ML aggregation unexpected output: {rows}")


@test
def t06_ml_train_sklearn():
    """ML training in a code node — train a model if sklearn/numpy available."""
    wf = create_workflow(f"E2E-ml-train-{uuid.uuid4().hex[:6]}")
    code = (
        "try:\n"
        "    import numpy as np\n"
        "    from sklearn.linear_model import LogisticRegression\n"
        "    rng = np.random.default_rng(0)\n"
        "    X = rng.random((200, 2))\n"
        "    y = (X[:, 0] + X[:, 1] > 1).astype(int)\n"
        "    model = LogisticRegression().fit(X, y)\n"
        "    acc = float(model.score(X, y))\n"
        "    output = {'trained': True, 'accuracy': acc}\n"
        "except ImportError as ex:\n"
        "    output = {'trained': False, 'missing': str(ex)}"
    )
    nodes = [
        n("trig", "manual_trigger", 0),
        n("train", "code", 1, code=code),
    ]
    edges = [e("trig", "train")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t06 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf, timeout=120)
    print(f"  run: {run['status']} | {summarize(run)}")
    o = main_value(node_out(run, "train"))
    if run["status"] != "success":
        issue(f"t06 ML train run not success: {summarize(run)}")
    elif isinstance(o, dict):
        if o.get("trained"):
            ok(f"t06 sklearn model trained, accuracy={o.get('accuracy'):.3f}")
        else:
            ok(f"t06 ML libs not in default env (expected): {o.get('missing')}")
    else:
        issue(f"t06 ML train unexpected output: {o}")


@test
def t07_http_request():
    """HTTP node against a public test endpoint."""
    wf = create_workflow(f"E2E-http-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0),
        n("http", "http_request", 1, url="https://httpbin.org/json", method="GET"),
    ]
    edges = [e("trig", "http")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t07 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf, timeout=60)
    print(f"  run: {run['status']} | {summarize(run)}")
    if run["status"] == "success":
        ok("t07 HTTP request node succeeded")
    else:
        # Network may be restricted in the container; report but don't hard-fail
        issue(f"t07 HTTP request failed (maybe egress blocked): {summarize(run)}")


@test
def t08_error_handling():
    """stop_and_error must fail the run with the message."""
    wf = create_workflow(f"E2E-err-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0),
        n("stop", "stop_and_error", 1, message="intentional failure"),
    ]
    edges = [e("trig", "stop")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t08 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    stop = node_out(run, "stop")
    if run["status"] == "error" and stop and stop["status"] == "error":
        ok("t08 stop_and_error correctly failed the run")
    else:
        issue(f"t08 stop_and_error did not fail as expected: {summarize(run)}")


@test
def t09_code_runtime_error():
    """A code node raising should surface a clear error, not crash the engine."""
    wf = create_workflow(f"E2E-coderr-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0),
        n("bad", "code", 1, code="output = 1 / 0"),
    ]
    edges = [e("trig", "bad")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t09 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    bad = node_out(run, "bad")
    if run["status"] == "error" and bad and "division" in (bad.get("error") or "").lower():
        ok("t09 code runtime error surfaced cleanly")
    elif run["status"] == "error":
        ok(f"t09 code error surfaced: {bad.get('error') if bad else 'n/a'}")
    else:
        issue(f"t09 code error not reported as run error: {summarize(run)}")


@test
def t10_large_dataset_expand_cap():
    """DatasetRef with >50k rows wired into a generic node must error with guidance."""
    wf = create_workflow(f"E2E-bigds-{uuid.uuid4().hex[:6]}")
    code = "output = [{'i': i} for i in range(60000)]"
    nodes = [
        n("trig", "manual_trigger", 0),
        n("gen", "code", 1, code=code),
        n("ds", "records_to_dataset", 2),
        n("loop", "loop_over_items", 3),
        n("each", "no_op", 4),
    ]
    edges = [e("trig", "gen"), e("gen", "ds"), e("ds", "loop"),
             e("loop", "each", source_output="item")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t10 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf, timeout=120)
    print(f"  run: {run['status']} | {summarize(run)}")
    loop = node_out(run, "loop")
    if run["status"] == "error" and loop and "row" in (loop.get("error") or "").lower():
        ok("t10 >50k dataset auto-expand correctly capped with guidance")
    elif run["status"] == "error":
        ok(f"t10 large dataset capped (error): {(loop.get('error') if loop else '')[:100]}")
    else:
        issue(f"t10 large dataset NOT capped — expanded 60k rows inline: {summarize(run)}")


@test
def t11_empty_graph_run():
    """Running an empty workflow should be a clean no-op or clear error, not a 500."""
    wf = create_workflow(f"E2E-empty-{uuid.uuid4().hex[:6]}")
    run = run_and_wait(wf)
    print(f"  run: {run.get('status')} | detail={run.get('detail','')[:120]}")
    if run.get("status") in ("success", "error"):
        ok(f"t11 empty workflow handled gracefully: {run['status']}")
    elif run.get("status") == "dispatch_error" and run.get("code") in (400, 409, 422):
        ok(f"t11 empty workflow rejected with {run['code']} (acceptable)")
    else:
        issue(f"t11 empty workflow produced unexpected result: {run}")


@test
def t12_csv_parse_aggregate():
    """csv_parse -> aggregate dataset stats."""
    wf = create_workflow(f"E2E-csv-{uuid.uuid4().hex[:6]}")
    nodes = [
        n("trig", "manual_trigger", 0),
        n("parse", "csv_parse", 1, text="name,amount\nAda,100\nBob,250\nCy,75\n", has_header=True),
        n("sql", "duckdb_sql", 2, sql="SELECT SUM(amount) AS total, AVG(amount) AS avg FROM input"),
        n("recs", "dataset_to_records", 3, max_rows=10),
    ]
    edges = [e("trig", "parse"), e("parse", "sql"), e("sql", "recs")]
    g = set_graph(wf, nodes, edges)
    if "_error" in g:
        issue(f"t12 set_graph failed: {g['_error']}")
        return
    run = run_and_wait(wf)
    print(f"  run: {run['status']} | {summarize(run)}")
    rows = main_value(node_out(run, "recs"))
    if run["status"] == "success" and isinstance(rows, list):
        ok(f"t12 csv parse + aggregate: {rows}")
    else:
        issue(f"t12 csv parse/aggregate failed: {summarize(run)}")


def main() -> int:
    print(f"=== Nodyra E2E test against {BASE} ===\n")
    for name, fn in TESTS:
        print(f"--- {name} ---")
        try:
            fn()
        except Exception as ex:  # noqa: BLE001
            issue(f"{name} raised {type(ex).__name__}: {ex}")
        print()
    print("=" * 60)
    print(f"PASSED: {len(PASSED)}    ISSUES: {len(ISSUES)}")
    if ISSUES:
        print("\nISSUES FOUND:")
        for i in ISSUES:
            print(f"  - {i}")
    return 1 if ISSUES else 0


if __name__ == "__main__":
    _configure_output()
    sys.exit(main())
