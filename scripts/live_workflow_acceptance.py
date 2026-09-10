"""Exercise realistic workflows through a running Nodyra installation.

Uses synthetic data, creates inspectable QA workflows, and writes a report without
credentials. Run only against a disposable acceptance environment. Authenticate
with NODYRA_TEST_EMAIL and NODYRA_TEST_PASSWORD (or NODYRA_TOKEN).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from first_run_smoke import fetch_artifact


class Acceptance:
    def __init__(self, base: str, report: Path):
        self.base = base.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=90)
        self.report = report
        self.results: list[dict] = []
        self.suffix = uuid.uuid4().hex[:8]
        deadline = time.monotonic() + 60
        while True:
            try:
                ready = self.client.get("/health/live", timeout=5)
                if ready.is_success:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Nodyra did not become ready within 60 seconds")
            time.sleep(1)
        token = os.environ.get("NODYRA_TOKEN")
        if not token:
            auth = self.client.post(
                "/auth/login",
                json={
                    "email": os.environ["NODYRA_TEST_EMAIL"],
                    "password": os.environ["NODYRA_TEST_PASSWORD"],
                },
            )
            auth.raise_for_status()
            token = auth.json()["token"]
        self.token = token
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.current: dict = {}

    def api(self, method: str, path: str, body=None):
        response = self.client.request(method, path, json=body)
        if response.is_error:
            raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:500]}")
        return response.json() if response.content else None

    def template(self, template: str, title: str):
        created = self.api(
            "POST",
            f"/templates/{template}/instantiate",
            {
                "name": f"QA · {title} · {self.suffix}",
            },
        )
        wid = created["id"]
        self.current.setdefault("workflow_ids", []).append(wid)
        return wid, self.api("GET", f"/workflows/{wid}")["graph"]

    def create(self, title: str, graph: dict, **settings):
        wid = self.api("POST", "/workflows", {"name": f"QA · {title} · {self.suffix}"})["id"]
        self.current.setdefault("workflow_ids", []).append(wid)
        self.api("PUT", f"/workflows/{wid}", {"graph": graph, **settings})
        return wid

    def wait(self, rid: str, expected="success", timeout=180):
        self.current.setdefault("run_ids", []).append(rid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.api("GET", f"/runs/{rid}")
            if run["status"] in {"success", "error", "cancelled", "timed_out", "failed"}:
                assert run["status"] == expected, json.dumps(
                    {
                        "status": run["status"],
                        "error": run.get("error"),
                        "nodes": [
                            {"node": n["node_id"], "status": n["status"], "error": n.get("error")}
                            for n in run.get("node_runs", [])
                            if n["status"] == "error"
                        ],
                    }
                )[:3000]
                return run
            time.sleep(0.5)
        raise AssertionError(f"Run {rid} did not finish in {timeout}s")

    def run(self, wid: str, expected="success"):
        return self.wait(self.api("POST", f"/workflows/{wid}/run", {})["run_id"], expected)

    @staticmethod
    def output(run: dict, node_id: str):
        rows = run.get("node_runs", run.get("results", []))
        row = next(n for n in rows if n["node_id"] == node_id)
        value = row.get("output")
        return value.get("main", value) if isinstance(value, dict) else value

    def case(self, name, action):
        self.current = {"case": name}
        start = time.monotonic()
        try:
            action()
            self.current["status"] = "passed"
        except Exception as exc:
            self.current.update(status="failed", error=str(exc)[:3500])
        self.current["seconds"] = round(time.monotonic() - start, 2)
        self.results.append(self.current)
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(json.dumps(self.results, indent=2), encoding="utf-8")
        print(f"{name}: {self.current['status']} ({self.current['seconds']}s)", flush=True)
        if self.current["status"] == "failed":
            print(self.current["error"][:700], flush=True)

    def customers(self):
        wid, _ = self.template("csv_clean_dedupe", "Customer import cleanup")
        run = self.run(wid)
        listing = self.api("GET", f"/artifacts?run_id={run['id']}")
        artifacts = listing if isinstance(listing, list) else listing["items"]
        artifact = next(a for a in artifacts if a["name"].endswith(".csv"))
        content = fetch_artifact(self.base, artifact["id"], self.token)
        assert hashlib.sha256(content).hexdigest() == artifact["checksum_sha256"]
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        assert sorted(r["email"] for r in rows) == [
            "ada@example.com",
            "alan@example.com",
            "grace@example.com",
        ], rows
        self.current["verified_rows"] = len(rows)

    def routing(self):
        wid, graph = self.template("conditional_routing", "Order approval routing")
        high = self.run(wid)
        assert self.output(high, "high_value")["route"] == "review"
        graph["nodes"][0]["params"]["data"]["amount"] = 25
        self.api("PUT", f"/workflows/{wid}", {"graph": graph})
        low = self.run(wid)
        assert self.output(low, "standard")["route"] == "auto-approve"

    def extract(self):
        wid, _ = self.template("text_extract_regex", "Invoice text extraction")
        assert self.output(self.run(wid), "shape") == [
            {"order": "A-1001", "total": 42.5},
            {"order": "A-1002", "total": 17.0},
        ]

    def health(self):
        wid, _ = self.template("http_health_check", "Public endpoint health monitor")
        assert self.output(self.run(wid), "check")["healthy"] is True

    def nonfinite_output(self):
        graph = self.chain(
            ("start", "manual_trigger", {}),
            (
                "metrics",
                "code",
                {
                    "code": "output = {'finite': 42.5, 'missing': float('nan'), "
                    "'bounds': [float('inf'), -float('inf')]}"
                },
            ),
        )
        wid = self.create("Read non-finite analytical results", graph)
        result = self.run(wid)
        assert self.output(result, "metrics") == {
            "finite": 42.5,
            "missing": None,
            "bounds": [None, None],
        }
        # Read the stored result again, rather than only the execution response.
        stored = self.api("GET", f"/runs/{result['id']}")
        assert self.output(stored, "metrics") == self.output(result, "metrics")

    @staticmethod
    def chain(*specs):
        nodes = [
            {"id": name, "type": kind, "params": params, "position": {"x": i * 280, "y": 40}}
            for i, (name, kind, params) in enumerate(specs)
        ]
        return {
            "nodes": nodes,
            "edges": [
                {"source": a["id"], "target": b["id"]}
                for a, b in zip(nodes, nodes[1:], strict=False)
            ],
        }

    def failure_recovery(self):
        handler, _ = self.template("error_handler_workflow", "Operations failure inbox")
        self.api("POST", f"/workflows/{handler}/publish", {})
        graph = self.chain(
            ("start", "manual_trigger", {"data": {"order_id": "QA-1042", "amount": 42}}),
            ("validate", "code", {"code": "raise ValueError('QA supplier unavailable')"}),
            ("invoice", "code", {"code": "output = {**input, 'invoiced': True}"}),
        )
        wid = self.create("Supplier failure and recovery", graph, error_workflow_id=handler)
        failed = self.run(wid, "error")
        assert any(
            "QA supplier unavailable" in (n.get("error") or "") for n in failed["node_runs"]
        ), failed
        assert (
            next(n for n in failed["node_runs"] if n["node_id"] == "invoice")["status"] == "skipped"
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            runs = self.api("GET", f"/workflows/{handler}/runs")["items"]
            matching = [r for r in runs if r.get("triggered_by_error_run_id") == failed["id"]]
            if matching:
                handled = self.wait(matching[0]["id"])
                summary = self.output(handled, "summarise")
                assert summary["run_id"] == failed["id"], summary
                assert summary["node"] == "validate", summary
                break
            time.sleep(0.5)
        else:
            raise AssertionError("Error workflow did not receive the failure")
        graph["nodes"][1]["params"]["code"] = "output = {**input, 'validated': True}"
        self.api("PUT", f"/workflows/{wid}", {"graph": graph})
        recovered = self.wait(self.api("POST", f"/runs/{failed['id']}/retry", {})["run_id"])
        assert self.output(recovered, "invoice") == {
            "order_id": "QA-1042",
            "amount": 42,
            "validated": True,
            "invoiced": True,
        }

    def cancellation(self):
        graph = self.chain(
            ("start", "manual_trigger", {"data": {}}),
            ("slow", "code", {"code": "import time\ntime.sleep(30)\noutput = 'finished'"}),
            ("after", "no_op", {}),
        )
        wid = self.create("Cancel a slow supplier request", graph)
        rid = self.api("POST", f"/workflows/{wid}/run", {})["run_id"]
        deadline = time.monotonic() + 20
        while self.api("GET", f"/runs/{rid}")["status"] != "running":
            assert time.monotonic() < deadline, "Run did not start"
            time.sleep(0.2)
        self.api("POST", f"/runs/{rid}/cancel", {})
        self.wait(rid, "cancelled", timeout=45)

    def dataset_retry(self):
        graph = self.chain(
            ("start", "manual_trigger", {"data": [{"customer": "Ada", "total": 120}]}),
            ("dataset", "records_to_dataset", {}),
            ("validate", "code", {"code": "raise ValueError('QA data review required')"}),
            ("records", "dataset_to_records", {}),
        )
        wid = self.create("Resume a persisted customer dataset", graph)
        failed = self.run(wid, "error")
        graph["nodes"][2]["params"]["code"] = "output = input"
        self.api("PUT", f"/workflows/{wid}", {"graph": graph})
        recovered = self.wait(self.api("POST", f"/runs/{failed['id']}/retry", {})["run_id"])
        assert self.output(recovered, "records") == [{"customer": "Ada", "total": 120}]
        # A stale/tampered editor cache is a client error, not a server crash.
        tampered = json.loads(json.dumps(self.output(failed, "dataset")))
        tampered["artifact"]["size_bytes"] += 1
        rejected = self.client.post(
            f"/workflows/{wid}/nodes/records/test",
            json={"cache": {"validate": {"main": tampered}}},
        )
        assert rejected.status_code == 400, rejected.text[:500]
        self.current["tampered_cache_status"] = rejected.status_code

    def subworkflow_dataset(self):
        env_id = os.environ.get("NODYRA_TEST_ALTERNATE_ENVIRONMENT_ID")
        if not env_id:
            env = self.api(
                "POST",
                "/environments",
                {
                    "name": f"QA isolated child boundary {self.suffix}",
                    "python_version": "3.12",
                    "packages": [],
                },
            )
            env_id = env["id"]
            self.current["environment_id"] = env_id
            deadline = time.monotonic() + 180
            while env["status"] in {"pending", "building", "queued"}:
                assert time.monotonic() < deadline, "Alternate environment did not finish building"
                time.sleep(2)
                env = self.api("GET", f"/environments/{env_id}")
            assert env["status"] == "ready", env["status"]
        child = self.create(
            "Reusable customer dataset",
            self.chain(
                ("start", "manual_trigger", {}),
                ("dataset", "records_to_dataset", {}),
            ),
        )
        parent = self.create(
            "Read dataset from a separate sandbox environment",
            self.chain(
                ("start", "manual_trigger", {"data": [{"customer": "Grace", "total": 250}]}),
                ("child", "execute_workflow", {"workflow_id": child}),
                ("records", "dataset_to_records", {}),
            ),
            environment_id=env_id,
        )
        result = self.run(parent)
        assert self.output(result, "records") == [{"customer": "Grace", "total": 250}]

    def timeout(self):
        graph = self.chain(
            ("start", "manual_trigger", {"data": {}}),
            ("slow", "code", {"code": "import time\ntime.sleep(10)\noutput = 'finished'"}),
        )
        wid = self.create("Bound a slow operation", graph, run_timeout_seconds=1)
        run = self.run(wid, "timed_out")
        assert run.get("error"), "A timed-out run must explain what happened"

    def subworkflow(self):
        child = self.create(
            "Reusable tax calculation",
            self.chain(
                ("start", "manual_trigger", {"data": {}}),
                (
                    "tax",
                    "code",
                    {"code": "output = {**input, 'tax': round(input['net'] * 0.1, 2)}"},
                ),
            ),
        )
        parent = self.create(
            "Order with reusable tax step",
            self.chain(
                ("start", "manual_trigger", {"data": {"net": 250, "order_id": "QA-TAX"}}),
                ("tax_step", "execute_workflow", {"workflow_id": child}),
                (
                    "total",
                    "code",
                    {"code": "output = {**input, 'total': input['net'] + input['tax']}"},
                ),
            ),
        )
        assert self.output(self.run(parent), "total")["total"] == 275

    def private_egress(self):
        wid = self.create(
            "Block metadata endpoint access",
            self.chain(
                ("start", "manual_trigger", {"data": {}}),
                ("request", "http_request", {"url": "http://169.254.169.254/latest/meta-data/"}),
            ),
        )
        run = self.run(wid, "error")
        assert any("blocked" in (n.get("error") or "") for n in run["node_runs"]), run

    def batch(self):
        wid, graph = self.template("batch_http_fetch", "Parallel supplier requests")
        graph["nodes"][0]["params"]["data"]["urls"].append("https://httpbin.org/status/404")
        self.api("PUT", f"/workflows/{wid}", {"graph": graph})
        run = self.run(wid)
        output = next(n["output"] for n in run["node_runs"] if n["node_id"] == "collect")
        assert len(output["results"]) == 2, output
        assert len(output["errors"]) == 1, output

    def api_export(self):
        wid, _ = self.template("json_api_to_csv", "Customer directory from API")
        run = self.run(wid)
        ref = self.output(run, "export")
        content = fetch_artifact(self.base, ref["artifact_id"], self.token)
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        assert len(rows) == 10, rows
        assert [r["name"] for r in rows] == sorted(r["name"] for r in rows), rows
        assert hashlib.sha256(content).hexdigest() == ref["checksum_sha256"]

    def webhook(self):
        wid, graph = self.template("webhook_validate_respond", "Validated order intake")
        path = f"qa-intake-{self.suffix}"
        secret = uuid.uuid4().hex
        graph["nodes"][0]["params"].update(
            path=path,
            http_method="POST",
            auth_type="bearer",
            auth_bearer_token=secret,
        )
        self.api("PUT", f"/workflows/{wid}", {"graph": graph, "active": True})
        self.api("POST", f"/workflows/{wid}/publish", {})
        try:
            auth = {"Authorization": f"Bearer {secret}"}
            denied = self.client.post(f"/webhook/{path}", json={}, headers={"Authorization": ""})
            assert denied.status_code in {401, 403}, denied.text
            valid = self.client.post(
                f"/webhook/{path}",
                json={"email": "buyer@example.com", "amount": 42.5},
                headers=auth,
            )
            assert valid.status_code == 200, valid.text
            invalid = self.client.post(f"/webhook/{path}", json={"amount": "bad"}, headers=auth)
            assert invalid.status_code == 422, (
                f"Expected 422, received {invalid.status_code}: {invalid.text}"
            )
            assert invalid.json()["errors"], invalid.text
        finally:
            self.api("PUT", f"/workflows/{wid}", {"active": False})

    def environment(self):
        env = self.api(
            "POST",
            "/environments",
            {
                "name": f"QA data-quality and Excel · {self.suffix}",
                "python_version": "3.12",
                "packages": [
                    "openpyxl>=3.1",
                    "pandas>=2.0",
                    "ydata-profiling>=4.0,<5",
                    "setuptools>=78.1.1,<81",
                ],
            },
        )
        self.data_env = env["id"]
        self.current["environment_id"] = env["id"]
        print(f"Building environment {env['id']}", flush=True)
        deadline = time.monotonic() + 540
        while env["status"] in {"pending", "building", "queued"}:
            assert time.monotonic() < deadline, "Environment build exceeded nine minutes"
            time.sleep(3)
            env = self.api("GET", f"/environments/{env['id']}")
        assert env["status"] == "ready", {
            k: env.get(k) for k in ("status", "build_error", "build_log")
        }

    def quality(self):
        env_id = getattr(self, "data_env", None) or os.environ["NODYRA_TEST_ENVIRONMENT_ID"]
        wid, graph = self.template("data_quality_gate", "Uploaded metrics quality gate")
        self.api("PUT", f"/workflows/{wid}", {"environment_id": env_id})
        read = next(n for n in graph["nodes"] if n["id"] == "read")
        for bad in (False, True):
            values = [100 + (i % 5) for i in range(20)] + ([10000] if bad else [])
            response = self.client.post(
                "/artifacts/upload",
                files={
                    "file": (
                        "metrics.csv",
                        "value\n" + "\n".join(str(v) for v in values),
                        "text/csv",
                    )
                },
            )
            assert response.is_success, response.text
            read["params"] = {"file": response.json()["id"], "has_header": True}
            self.api("PUT", f"/workflows/{wid}", {"graph": graph})
            run = self.run(wid, "error" if bad else "success")
            if bad:
                assert any(
                    "quality gate failed" in (n.get("error") or "") for n in run["node_runs"]
                ), run
            else:
                assert self.output(run, "gate")["quality_passed"] is True
            artifacts = self.api("GET", f"/artifacts?run_id={run['id']}")["items"]
            assert any("html" in a["content_type"] for a in artifacts), artifacts

    def snapshot(self):
        import openpyxl

        env_id = getattr(self, "data_env", None) or os.environ["NODYRA_TEST_ENVIRONMENT_ID"]
        wid, _ = self.template("scheduled_dataset_snapshot", "Nightly Excel snapshot")
        self.api("PUT", f"/workflows/{wid}", {"environment_id": env_id})
        run = self.run(wid)
        ref = self.output(run, "snapshot")
        blob = fetch_artifact(self.base, ref["artifact_id"], self.token)
        assert ref["name"].startswith("snapshot-20"), ref
        assert hashlib.sha256(blob).hexdigest() == ref["checksum_sha256"]
        book = openpyxl.load_workbook(io.BytesIO(blob), read_only=True)
        try:
            rows = list(book["snapshot"].values)
            assert len(rows) == 101, len(rows)
            assert "snapshot_date" in rows[0], rows[0]
        finally:
            book.close()

    def scheduler(self):
        graph = self.chain(
            ("clock", "schedule_trigger", {"interval": "minutes", "every": 1, "tz": "UTC"}),
            ("report", "code", {"code": "output = {'scheduled': True, 'orders': 12}"}),
        )
        wid = self.create("Automatic minute report", graph, active=True)
        self.api("POST", f"/workflows/{wid}/publish", {})
        deadline = time.monotonic() + 95
        try:
            while time.monotonic() < deadline:
                runs = self.api("GET", f"/workflows/{wid}/runs")["items"]
                scheduled = [r for r in runs if r["trigger_type"] == "schedule"]
                if scheduled:
                    run = self.wait(scheduled[0]["id"])
                    assert self.output(run, "report")["scheduled"] is True
                    return
                time.sleep(2)
            raise AssertionError("Published schedule did not fire within 95 seconds")
        finally:
            self.api("PUT", f"/workflows/{wid}", {"active": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5188/api")
    parser.add_argument("--report", type=Path, default=Path(".tmp/live-workflow-acceptance.json"))
    parser.add_argument(
        "--cases",
        default="customers,routing,extract,health,webhook,failure_recovery,cancellation,timeout,subworkflow,private_egress,batch,api_export",
    )
    args = parser.parse_args()
    suite = Acceptance(args.base_url, args.report)
    try:
        for name in args.cases.split(","):
            suite.case(name, getattr(suite, name))
    finally:
        suite.client.close()
    return int(any(r["status"] != "passed" for r in suite.results))


if __name__ == "__main__":
    raise SystemExit(main())
