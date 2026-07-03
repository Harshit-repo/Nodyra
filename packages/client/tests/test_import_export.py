from __future__ import annotations

import json as _json

from click.testing import CliRunner
from nodyra_client.cli import main as cli_main


def _env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")


def test_workflow_import_posts_source(httpx_mock, monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    src = tmp_path / "flow.module.py"
    src.write_text("# nodyra module export\n")
    httpx_mock.add_response(
        method="POST",
        url="http://api.test/import",
        json={"id": "wf_9", "name": "Imported"},
        status_code=201,
    )
    result = CliRunner().invoke(
        cli_main.main,
        ["workflow", "import", str(src), "--name", "Imported"],
    )
    assert result.exit_code == 0, result.output
    req = httpx_mock.get_requests()[0]
    assert _json.loads(req.content) == {
        "source": "# nodyra module export\n",
        "name": "Imported",
    }


def test_export_docker_writes_zip(httpx_mock, monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    httpx_mock.add_response(
        url="http://api.test/workflows/wf_1/export/docker",
        content=b"PK\x03\x04fakezip",
        headers={"content-type": "application/zip"},
    )
    out = tmp_path / "wf.zip"
    result = CliRunner().invoke(
        cli_main.main,
        ["export", "docker", "wf_1", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"PK")


def test_export_docker_requires_output(monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli_main.main, ["export", "docker", "wf_1"])
    assert result.exit_code != 0
    assert "-o" in result.output or "--output" in result.output
