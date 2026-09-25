from __future__ import annotations

import pytest
from nodyra_client.client import NodyraClient, NodyraError

_RUN = {"id": "r1", "workflow_id": "wf1", "mode": "manual"}


def _detail(status: str) -> dict:
    return {**_RUN, "status": status, "node_runs": []}


def test_watch_yields_until_terminal(httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    for status in ("queued", "running", "success"):
        httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail(status))
    with NodyraClient(base_url="http://api.test", token="t") as client:
        seen = [detail.status for detail in client.runs.watch("r1", interval=0)]
    assert seen == ["queued", "running", "success"]


def test_watch_stops_on_timed_out(httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    for status in ("running", "timed_out"):
        httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail(status))
    with NodyraClient(base_url="http://api.test", token="t") as client:
        seen = [detail.status for detail in client.runs.watch("r1", interval=0)]
    assert seen == ["running", "timed_out"]


def test_watch_timeout_raises(httpx_mock, monkeypatch) -> None:
    clock = iter([0.0, 10.0, 20.0, 30.0, 40.0])
    monkeypatch.setattr("time.monotonic", lambda: next(clock))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    httpx_mock.add_response(
        url="http://api.test/runs/r1",
        json=_detail("running"),
        is_reusable=True,
    )
    with NodyraClient(base_url="http://api.test", token="t") as client:
        with pytest.raises(NodyraError) as exc_info:
            list(client.runs.watch("r1", interval=0, timeout=15.0))
    assert exc_info.value.status == 408


def test_run_watch_cli_exit_codes(httpx_mock, monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner
    from nodyra_client.cli import main as cli_main

    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    for status in ("running", "error"):
        httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail(status))
    result = CliRunner().invoke(
        cli_main.main,
        ["run", "watch", "r1", "--interval", "0"],
    )
    assert result.exit_code == 2, result.output
