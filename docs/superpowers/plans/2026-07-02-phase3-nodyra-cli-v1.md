# Phase 3 — Nodyra CLI & SDK v1 (PyPI Production Pass)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take `packages/client` (`nodyra-client`, currently a solid skeleton) to a publishable v1: human-friendly rich output with a `--json` escape hatch, `run watch` live progress, script import, docker export, robust auth UX, real docs, and a tag-driven PyPI release lane.

**Architecture:** The typed `NodyraClient` (httpx + pydantic) stays the single API surface; the click CLI grows a small render layer (rich tables vs JSON) and a polling `runs.watch()` iterator in the client so SDK users get it too. Packaging via hatchling; publishing via GitHub Actions trusted publishing on `client-v*` tags.

**Tech Stack:** Python ≥3.12, httpx, click, pydantic v2, rich; pytest + pytest-httpx + click.testing.CliRunner.

**Parent plan:** `docs/superpowers/plans/2026-07-02-nodyra-master-roadmap.md`
**Prerequisite:** Phase 2 merged (repo is Nodyra-named). All paths below are post-rename; the client package was already `nodyra_client` so its paths are stable.

## Global Constraints

- Runtime deps stay exactly: `httpx`, `click`, `pydantic`, `rich` — no additions.
- Every list/detail command honours the root `--json` flag (`nodyra --json ...`, also env `NODYRA_JSON=1`); JSON output must be machine-stable (no rich markup). Watch commands in JSON mode emit newline-delimited JSON snapshots and never start a Rich live renderer.
- Exit codes: `0` success, `1` client/API error, `2` watched run ended in `error`, `3` watched run ended `cancelled`.
- Windows-safe: no ANSI assumptions in tests (use `CliRunner`), token file perms best-effort on NT (existing behavior).
- Tests: `uv run pytest packages/client/tests -q` green after every task; `uv run ruff check packages/client` clean before each commit.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

**Key existing interfaces (already in the repo — consume, don't re-create):**
- `NodyraClient` (`packages/client/nodyra_client/client.py`): `workflows.{list,get,create,update,delete,publish}`, `runs.{start,get,cancel,list}`, `credentials.{list,create}`, `export_.{as_script,as_module}`, `whoami()`, internal `_get/_post/_put/_delete/_check`, `NodyraError(status, detail)`.
- Models (`nodyra_client/models.py`): `WorkflowSummary`, `WorkflowDetail`, `RunCreated`, `RunSummary`, `RunDetail(node_runs: list[NodeRunSummary])`, `CredentialSummary`.
- CLI (`nodyra_client/cli/main.py`): groups `workflow`, `run`, `export`, `credential`; `_TOKEN_FILE`, `_write_token_file`, `_client()`, `_render()`, `_handle_error()`.
- API endpoints: `POST /import` (body `{"source": str, "name": str}` → 201, import a `.module.py` export), `GET /workflows/{id}/export/docker` (zip bytes), run statuses `queued|running|success|error|cancelled`.

---

### Task 1: Package metadata + typing marker

**Files:**
- Modify: `packages/client/pyproject.toml`
- Create: `packages/client/nodyra_client/py.typed`, `packages/client/LICENSE`

- [ ] **Step 1:** Update `pyproject.toml` `[project]` (keep existing deps/scripts):

```toml
[project]
name = "nodyra-client"
version = "0.2.0"
description = "Python client and CLI for Nodyra — the Python-native workflow automation platform"
readme = "README.md"
license = { text = "Apache-2.0" }
requires-python = ">=3.12"
keywords = ["workflow", "automation", "nodyra", "orchestration", "mcp"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries",
    "Typing :: Typed",
]

[project.urls]
Homepage = "https://github.com/<owner>/nodyra"
Documentation = "https://github.com/<owner>/nodyra/blob/main/packages/client/README.md"
Changelog = "https://github.com/<owner>/nodyra/blob/main/CHANGELOG.md"
```

Substitute the real GitHub owner (check `git remote get-url origin`). Copy the repo-root `LICENSE` into `packages/client/LICENSE` (sdists must self-contain their license; verify root license is Apache-2.0 with `head -3 LICENSE` — if the repo uses the Sustainable Use License per the release plan, mirror *that* text and fix the classifier/`license` field to match. **Do not guess: read the root LICENSE.**)

- [ ] **Step 2:** Create empty `packages/client/nodyra_client/py.typed` and add to the wheel:

```toml
[tool.hatch.build.targets.wheel]
packages = ["nodyra_client"]

[tool.hatch.build.targets.sdist]
include = ["nodyra_client", "README.md", "LICENSE", "tests"]
```

- [ ] **Step 3: Build smoke**

Run: `uv build --package nodyra-client && uvx twine check dist/*`
Expected: wheel + sdist build; twine reports PASSED for both.

- [ ] **Step 4: Commit**

```bash
git add packages/client/pyproject.toml packages/client/nodyra_client/py.typed packages/client/LICENSE
git commit -m "chore(client): PyPI-grade metadata, py.typed, packaged license

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---### Task 2: Render layer — rich tables by default, `--json` for machines

**Files:**
- Create: `packages/client/nodyra_client/cli/render.py`
- Modify: `packages/client/nodyra_client/cli/main.py` (group callback + every command's output call)
- Test: `packages/client/tests/test_render.py` (new), extend `packages/client/tests/test_cli.py`

**Interfaces:**
- Produces: `emit(data, *, json_mode: bool, columns: list[tuple[str, str]] | None = None) -> None` — `columns` is `(header, attribute)` pairs; when `columns` given, `data` is a list of pydantic models rendered as a table (JSON when `json_mode`). Later tasks call `emit` for all output.

- [ ] **Step 1: Write failing tests** `packages/client/tests/test_render.py`:

```python
from __future__ import annotations

import json

from click.testing import CliRunner
from pydantic import BaseModel


class _Row(BaseModel):
    id: str
    name: str
    status: str


def _rows() -> list[_Row]:
    return [
        _Row(id="wf_1", name="Daily ETL", status="published"),
        _Row(id="wf_2", name="Alerts", status="draft"),
    ]


def test_emit_table_mode(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(_rows(), json_mode=False,
         columns=[("ID", "id"), ("Name", "name"), ("Status", "status")])
    out = capsys.readouterr().out
    assert "Daily ETL" in out and "wf_2" in out
    assert "ID" in out  # header rendered


def test_emit_json_mode_is_parseable(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(_rows(), json_mode=True,
         columns=[("ID", "id"), ("Name", "name"), ("Status", "status")])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "wf_1"


def test_emit_scalar_none_prints_ok(capsys) -> None:
    from nodyra_client.cli.render import emit

    emit(None, json_mode=False)
    assert capsys.readouterr().out.strip() == "OK"
```

Run: `uv run pytest packages/client/tests/test_render.py -v` → FAIL (module missing).

- [ ] **Step 2: Implement `nodyra_client/cli/render.py`:**

```python
"""CLI output: rich tables for humans, plain JSON for machines."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from rich.console import Console
from rich.table import Table

_console = Console()


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_plain(o) for o in obj]
    return obj


def _cell(item: Any, attr: str) -> str:
    val = getattr(item, attr, None) if hasattr(item, attr) else (
        item.get(attr) if isinstance(item, dict) else None
    )
    if val is None:
        return "-"
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    return str(val)


def emit(
    data: Any,
    *,
    json_mode: bool,
    columns: Sequence[tuple[str, str]] | None = None,
) -> None:
    if json_mode:
        print(json.dumps(_plain(data), indent=2, default=str))
        return
    if data is None:
        print("OK")
        return
    if columns is not None and isinstance(data, list):
        table = Table(header_style="bold")
        for header, _ in columns:
            table.add_column(header)
        for item in data:
            table.add_row(*[_cell(item, attr) for _, attr in columns])
        _console.print(table)
        return
    print(json.dumps(_plain(data), indent=2, default=str))


def emit_json_line(data: Any) -> None:
    """Emit one compact JSON object for streaming/NDJSON use cases."""
    print(json.dumps(_plain(data), separators=(",", ":"), default=str), flush=True)
```

- [ ] **Step 3:** Run: `uv run pytest packages/client/tests/test_render.py -v` → PASS.

- [ ] **Step 4: Wire the global flag.** In `cli/main.py`, change the group and list commands:

```python
@click.group()
@click.version_option(__version__, prog_name="nodyra")
@click.option(
    "--json", "json_mode", is_flag=True, envvar="NODYRA_JSON",
    help="Machine-readable JSON output.",
)
@click.pass_context
def main(ctx: click.Context, json_mode: bool) -> None:
    """nodyra — CLI for the Nodyra workflow automation platform. ..."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
```

Add a helper next to `_client()`:

```python
def _json_mode() -> bool:
    ctx = click.get_current_context(silent=True)
    while ctx is not None:
        if isinstance(ctx.obj, dict) and "json" in ctx.obj:
            return bool(ctx.obj["json"])
        ctx = ctx.parent
    return False
```

Replace every `_render(x)` call with `emit(x, json_mode=_json_mode(), columns=...)`, deleting `_render`/`_simplify`. Column sets:

```python
_WF_COLUMNS = [("ID", "id"), ("Name", "name"), ("Status", "status"),
               ("Version", "latest_version"), ("Updated", "updated_at")]
_RUN_COLUMNS = [("ID", "id"), ("Workflow", "workflow_name"), ("Status", "status"),
                ("Mode", "mode"), ("Started", "started_at"),
                ("Duration (s)", "duration_seconds")]
_CRED_COLUMNS = [("ID", "id"), ("Name", "name"), ("Type", "type"), ("Scope", "scope")]
```

`workflow list` → `_WF_COLUMNS`; `run list` → `_RUN_COLUMNS`; `credential list` → `_CRED_COLUMNS`; detail commands (`get`, `whoami`, `create`, etc.) pass no columns (JSON body either mode).

- [ ] **Step 5: Extend `tests/test_cli.py`** with command-level coverage (pytest-httpx is already a dev dep):

```python
def test_workflow_list_renders_table(httpx_mock, monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner

    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")
    httpx_mock.add_response(
        url="http://api.test/workflows?limit=50&offset=0",
        json=[{"id": "wf_1", "name": "Daily ETL", "active": True,
               "status": "published", "latest_version": 3}],
    )
    result = CliRunner().invoke(cli_main.main, ["workflow", "list"])
    assert result.exit_code == 0, result.output
    assert "Daily ETL" in result.output


def test_workflow_list_json_flag(httpx_mock, monkeypatch, tmp_path) -> None:
    import json as _json

    from click.testing import CliRunner

    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")
    httpx_mock.add_response(
        url="http://api.test/workflows?limit=50&offset=0",
        json=[{"id": "wf_1", "name": "Daily ETL", "active": True,
               "status": "published"}],
    )
    result = CliRunner().invoke(cli_main.main, ["--json", "workflow", "list"])
    assert result.exit_code == 0, result.output
    assert _json.loads(result.output)[0]["id"] == "wf_1"
```

- [ ] **Step 6:** Run: `uv run pytest packages/client/tests -q` → all PASS. `uv run ruff check packages/client` → clean.

- [ ] **Step 7: Commit**

```bash
git add packages/client
git commit -m "feat(cli): rich table output with global --json flag

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Client hardening — connect retries + actionable auth errors

**Files:**
- Modify: `packages/client/nodyra_client/client.py`
- Modify: `packages/client/nodyra_client/cli/main.py` (`_handle_error`)
- Test: `packages/client/tests/test_client_errors.py` (new)

**Interfaces:**
- Produces: `NodyraError` gains `.hint: str | None`; `NodyraClient.__init__` uses `httpx.HTTPTransport(retries=2)`.

- [ ] **Step 1: Failing tests** `packages/client/tests/test_client_errors.py`:

```python
from __future__ import annotations

import pytest

from nodyra_client.client import NodyraClient, NodyraError


def test_401_carries_login_hint(httpx_mock) -> None:
    httpx_mock.add_response(status_code=401, json={"detail": "Not authenticated"})
    with NodyraClient(base_url="http://api.test", token="bad") as c:
        with pytest.raises(NodyraError) as ei:
            c.whoami()
    assert ei.value.status == 401
    assert "nodyra login" in (ei.value.hint or "")


def test_403_carries_scope_hint(httpx_mock) -> None:
    httpx_mock.add_response(status_code=403, json={"detail": "missing scope workflow:write"})
    with NodyraClient(base_url="http://api.test", token="t") as c:
        with pytest.raises(NodyraError) as ei:
            c.workflows.create(name="x")
    assert "scope" in (ei.value.hint or "").lower()


def test_transport_uses_connect_retries() -> None:
    with NodyraClient(base_url="http://api.test") as c:
        transport = c._client._transport
        assert transport._pool._retries == 2  # httpx HTTPTransport internal
```

(If the last assertion's internals differ in the pinned httpx version, assert construction instead: monkeypatch `httpx.HTTPTransport` to record `retries` and instantiate the client.)

Run: `uv run pytest packages/client/tests/test_client_errors.py -v` → FAIL.

- [ ] **Step 2: Implement.** In `client.py`:

```python
class NodyraError(Exception):
    """Raised when the API returns a non-2xx status."""

    def __init__(self, status: int, detail: str, hint: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.hint = hint
        super().__init__(f"[{status}] {detail}")
```

In `_check`, before raising:

```python
        hint = None
        if r.status_code == 401:
            hint = "Authentication failed — run 'nodyra login' or set NODYRA_TOKEN."
        elif r.status_code == 403:
            hint = ("Permission denied — the token lacks a required scope/role. "
                    "Create a token with the needed scopes in Settings → API tokens.")
        raise NodyraError(r.status_code, detail, hint)
```

In `__init__`, pass the retrying transport (connect-level retries only — safe for idempotent and non-idempotent calls alike because httpx retries only connection establishment, never a sent request):

```python
        self._client = httpx.Client(
            base_url=self._base,
            timeout=self._timeout,
            headers=self._headers(),
            transport=httpx.HTTPTransport(retries=2),
        )
```

In `cli/main.py` `_handle_error`:

```python
def _handle_error(exc: Exception) -> None:
    click.echo(f"Error: {exc}", err=True)
    hint = getattr(exc, "hint", None)
    if hint:
        click.echo(f"Hint: {hint}", err=True)
    sys.exit(1)
```

- [ ] **Step 3:** Run: `uv run pytest packages/client/tests -q` → PASS; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add packages/client
git commit -m "feat(client): connect retries and actionable 401/403 hints

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `runs.watch()` in the SDK + `nodyra run watch` / `run start --watch`

**Files:**
- Modify: `packages/client/nodyra_client/client.py` (`_RunsAPI`)
- Modify: `packages/client/nodyra_client/cli/main.py` (`run` group)
- Test: `packages/client/tests/test_run_watch.py` (new)

**Interfaces:**
- Produces: `_RunsAPI.watch(run_id, *, interval: float = 1.5, timeout: float | None = None) -> Iterator[RunDetail]` — yields snapshots (always yields the terminal one last); raises `NodyraError(408, ...)` on timeout. CLI exit codes per Global Constraints.

- [ ] **Step 1: Failing tests** `packages/client/tests/test_run_watch.py`:

```python
from __future__ import annotations

import pytest

from nodyra_client.client import NodyraClient, NodyraError

_RUN = {"id": "r1", "workflow_id": "wf1", "mode": "manual"}


def _detail(status: str) -> dict:
    return {**_RUN, "status": status, "node_runs": []}


def test_watch_yields_until_terminal(httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)
    for status in ("queued", "running", "success"):
        httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail(status))
    with NodyraClient(base_url="http://api.test", token="t") as c:
        seen = [d.status for d in c.runs.watch("r1", interval=0)]
    assert seen == ["queued", "running", "success"]


def test_watch_timeout_raises(httpx_mock, monkeypatch) -> None:
    clock = iter([0.0, 10.0, 20.0, 30.0, 40.0])
    monkeypatch.setattr("time.monotonic", lambda: next(clock))
    monkeypatch.setattr("time.sleep", lambda s: None)
    httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail("running"),
                            is_reusable=True)
    with NodyraClient(base_url="http://api.test", token="t") as c:
        with pytest.raises(NodyraError) as ei:
            list(c.runs.watch("r1", interval=0, timeout=15.0))
    assert ei.value.status == 408


def test_run_watch_cli_exit_codes(httpx_mock, monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner

    from nodyra_client.cli import main as cli_main

    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr("time.sleep", lambda s: None)
    for status in ("running", "error"):
        httpx_mock.add_response(url="http://api.test/runs/r1", json=_detail(status))
    result = CliRunner().invoke(cli_main.main, ["run", "watch", "r1", "--interval", "0"])
    assert result.exit_code == 2, result.output
```

Run: `uv run pytest packages/client/tests/test_run_watch.py -v` → FAIL (`watch` missing).

- [ ] **Step 2: Implement in `client.py`.** Add near the top: `import time` and `from typing import Iterator`; in module scope:

```python
TERMINAL_RUN_STATUSES = frozenset({"success", "error", "cancelled"})
```

In `_RunsAPI`:

```python
    def watch(
        self,
        run_id: str,
        *,
        interval: float = 1.5,
        timeout: float | None = None,
    ) -> Iterator[RunDetail]:
        """Poll the run until it reaches a terminal status.

        Yields every snapshot (including the terminal one). Raises
        ``NodyraError(408, ...)`` if *timeout* seconds elapse first.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            detail = self.get(run_id)
            yield detail
            if detail.status in TERMINAL_RUN_STATUSES:
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise NodyraError(408, f"run {run_id} still {detail.status} after {timeout}s")
            time.sleep(interval)
```

- [ ] **Step 3: CLI commands.** In `cli/main.py` add a shared renderer + two entry points:

```python
_EXIT_BY_STATUS = {"success": 0, "error": 2, "cancelled": 3}


def _watch_run(run_id: str, interval: float) -> None:
    from rich.live import Live
    from rich.table import Table

    from nodyra_client.cli.render import _console

    client = _client()
    final = None
    try:
        if _json_mode():
            from nodyra_client.cli.render import emit_json_line

            for snap in client.runs.watch(run_id, interval=interval):
                emit_json_line(snap)
                final = snap
            sys.exit(_EXIT_BY_STATUS.get(final.status if final else "", 1))
        with Live(console=_console, refresh_per_second=4) as live:
            for snap in client.runs.watch(run_id, interval=interval):
                table = Table(title=f"run {snap.id} — {snap.status}", header_style="bold")
                for col in ("Node", "Type", "Status", "Error"):
                    table.add_column(col)
                for nr in snap.node_runs:
                    table.add_row(nr.node_name or nr.node_id, nr.node_type,
                                  nr.status, (nr.error or "")[:60])
                live.update(table)
                final = snap
    except KeyboardInterrupt:
        click.echo(f"\nDetached. The run continues server-side; "
                   f"'nodyra run cancel {run_id}' to stop it.", err=True)
        sys.exit(130)
    sys.exit(_EXIT_BY_STATUS.get(final.status if final else "", 1))


@run.command("watch")
@click.argument("run_id")
@click.option("--interval", default=1.5, show_default=True, type=float)
def run_watch(run_id: str, interval: float) -> None:
    """Live-follow a run until it finishes (exit 0/2/3 by outcome)."""
    try:
        _watch_run(run_id, interval)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
```

and extend `run start` with `--watch`:

```python
@run.command("start")
@click.argument("workflow_id")
@click.option("--data", default=None, help="JSON input data for the run")
@click.option("--watch", "watch_", is_flag=True, help="Follow the run to completion.")
def run_start(workflow_id: str, data: str | None, watch_: bool) -> None:
    ...existing payload parsing...
    try:
        created = _client().runs.start(workflow_id, data=payload)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
        return
    if not watch_:
        emit(created, json_mode=_json_mode())
        return
    if not _json_mode():
        click.echo(f"run {created.id} started; watching...", err=True)
    try:
        _watch_run(created.id, 1.5)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
```

Note `rich.live` degrades gracefully in non-TTY (CliRunner) environments — the exit-code test above stays deterministic.

- [ ] **Step 4:** Run: `uv run pytest packages/client/tests -q` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(cli): run watch with live node table and outcome exit codes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `workflow import` + `export docker`

**Files:**
- Modify: `packages/client/nodyra_client/client.py` (`_WorkflowsAPI.import_module`, `_ExportAPI.as_docker_zip`)
- Modify: `packages/client/nodyra_client/cli/main.py`
- Test: `packages/client/tests/test_import_export.py` (new)

**Interfaces:**
- Consumes API: `POST /import` `{"source": str, "name": str}` → 201 JSON; `GET /workflows/{id}/export/docker` → zip bytes.
- Produces: `workflows.import_module(source: str, *, name: str) -> dict`; `export_.as_docker_zip(workflow_id: str) -> bytes`.

- [ ] **Step 1: Failing tests** `packages/client/tests/test_import_export.py`:

```python
from __future__ import annotations

from click.testing import CliRunner

from nodyra_client.cli import main as cli_main


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("NODYRA_BASE_URL", "http://api.test")
    monkeypatch.setenv("NODYRA_TOKEN", "t")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", tmp_path / "token")


def test_workflow_import_posts_source(httpx_mock, monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    src = tmp_path / "flow.module.py"
    src.write_text("# nodyra module export\n")
    httpx_mock.add_response(
        method="POST", url="http://api.test/import",
        json={"id": "wf_9", "name": "Imported"}, status_code=201,
    )
    result = CliRunner().invoke(
        cli_main.main, ["workflow", "import", str(src), "--name", "Imported"]
    )
    assert result.exit_code == 0, result.output
    req = httpx_mock.get_requests()[0]
    import json as _json
    body = _json.loads(req.content)
    assert body == {"source": "# nodyra module export\n", "name": "Imported"}


def test_export_docker_writes_zip(httpx_mock, monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    httpx_mock.add_response(
        url="http://api.test/workflows/wf_1/export/docker",
        content=b"PK\x03\x04fakezip",
        headers={"content-type": "application/zip"},
    )
    out = tmp_path / "wf.zip"
    result = CliRunner().invoke(
        cli_main.main, ["export", "docker", "wf_1", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"PK")


def test_export_docker_requires_output(monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli_main.main, ["export", "docker", "wf_1"])
    assert result.exit_code != 0
    assert "-o" in result.output or "--output" in result.output
```

Run: `uv run pytest packages/client/tests/test_import_export.py -v` → FAIL.

- [ ] **Step 2: Implement client methods.** In `_WorkflowsAPI`:

```python
    def import_module(self, source: str, *, name: str) -> dict[str, Any]:
        """Create a workflow from a ``.module.py`` export (AST-parsed
        server-side; no code executes)."""
        return self._c._post("/import", {"source": source, "name": name})
```

In `_ExportAPI`:

```python
    def as_docker_zip(self, workflow_id: str) -> bytes:
        r = self._c._client.get(f"/workflows/{workflow_id}/export/docker")
        if not r.is_success:
            self._c._check(r)  # raises NodyraError with the API detail
        return r.content
```

- [ ] **Step 3: CLI commands.** Under the `workflow` group:

```python
@workflow.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", required=True, help="Name for the imported workflow")
def workflow_import(path: Path, name: str) -> None:
    """Import a .module.py export as a new workflow (nothing is executed)."""
    try:
        result = _client().workflows.import_module(path.read_text(encoding="utf-8"),
                                                   name=name)
        emit(result, json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
```

Under `export`:

```python
@export.command("docker")
@click.argument("workflow_id")
@click.option("--output", "-o", required=True, help="Output .zip path")
def export_docker(workflow_id: str, output: str) -> None:
    """Export a workflow as a self-contained Docker bundle (zip)."""
    try:
        blob = _client().export_.as_docker_zip(workflow_id)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
        return
    Path(output).write_bytes(blob)
    click.echo(f"Written {len(blob)} bytes to {output}")
```

- [ ] **Step 4:** Run: `uv run pytest packages/client/tests -q` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(cli): workflow import from .module.py and docker-bundle export

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Auth UX — verify-before-save login, `--token-stdin`, `logout`

**Files:**
- Modify: `packages/client/nodyra_client/cli/main.py` (`login`, new `logout`)
- Test: extend `packages/client/tests/test_cli.py`

- [ ] **Step 1: Failing tests** (append to `test_cli.py`):

```python
def test_login_verifies_before_saving(httpx_mock, monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner

    token_file = tmp_path / ".nodyra" / "token"
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)
    httpx_mock.add_response(status_code=401, json={"detail": "bad token"})
    result = CliRunner().invoke(
        cli_main.main,
        ["login", "--base-url", "http://api.test", "--token", "bad"],
    )
    assert result.exit_code == 1
    assert not token_file.exists()  # nothing persisted on failed verification


def test_login_token_stdin(httpx_mock, monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner

    token_file = tmp_path / ".nodyra" / "token"
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)
    httpx_mock.add_response(json={"email": "ci@example.com"})
    result = CliRunner().invoke(
        cli_main.main,
        ["login", "--base-url", "http://api.test", "--token-stdin"],
        input="ndpat_secret\n",
    )
    assert result.exit_code == 0, result.output
    assert "ci@example.com" in result.output
    import json as _json
    assert _json.loads(token_file.read_text())["token"] == "ndpat_secret"


def test_logout_removes_token_file(monkeypatch, tmp_path) -> None:
    from click.testing import CliRunner

    token_file = tmp_path / ".nodyra" / "token"
    token_file.parent.mkdir()
    token_file.write_text("{}")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)
    result = CliRunner().invoke(cli_main.main, ["logout"])
    assert result.exit_code == 0
    assert not token_file.exists()
```

Run: `uv run pytest packages/client/tests/test_cli.py -v` → new tests FAIL.

- [ ] **Step 2: Implement.** Replace `login` and add `logout`:

```python
@main.command()
@click.option("--base-url", prompt=True, help="Nodyra instance URL")
@click.option("--token", default=None, help="Bearer token (omit to be prompted)")
@click.option("--token-stdin", is_flag=True,
              help="Read the token from stdin (for CI: echo $TOKEN | nodyra login ...)")
@click.option("--no-verify", is_flag=True,
              help="Save without calling /auth/me (offline instances)")
def login(base_url: str, token: str | None, token_stdin: bool, no_verify: bool) -> None:
    """Verify and save credentials to ~/.nodyra/token."""
    if token_stdin:
        token = sys.stdin.readline().strip()
    if not token:
        token = click.prompt("Token", hide_input=True)
    base_url = base_url.rstrip("/")
    if not no_verify:
        with NodyraClient(base_url=base_url, token=token) as client:
            try:
                user = client.whoami()
            except (NodyraError, httpx.TransportError) as exc:
                click.echo(f"Error: token verification failed, nothing saved: {exc}",
                           err=True)
                sys.exit(1)
        click.echo(f"Authenticated as {user.get('email', 'unknown')}")
    payload = json.dumps({"base_url": base_url, "token": token}).encode()
    _write_token_file(payload)
    click.echo(f"Credentials saved to {_TOKEN_FILE}")


@main.command()
def logout() -> None:
    """Remove saved credentials."""
    try:
        _TOKEN_FILE.unlink()
        click.echo("Logged out.")
    except FileNotFoundError:
        click.echo("No saved credentials.")
```

Delete the old `login` body (the previous behavior saved first and only warned on a bad token).

- [ ] **Step 3:** Run: `uv run pytest packages/client/tests -q` → PASS; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add packages/client
git commit -m "feat(cli): verify-before-save login, --token-stdin for CI, logout

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Rewrite: `packages/client/README.md` (this is the PyPI page)
- Modify: root `README.md` (add a CLI section pointing at it)

- [ ] **Step 1: Write `packages/client/README.md`:**

````markdown
# nodyra-client

Python SDK and `nodyra` CLI for [Nodyra](https://github.com/<owner>/nodyra) —
the Python-native, self-hostable workflow automation platform.

```bash
pip install nodyra-client
```

## CLI quickstart

```bash
nodyra login --base-url https://nodyra.example.com   # prompts for a token
nodyra workflow list
nodyra run start <workflow-id> --data '{"city": "Berlin"}' --watch
nodyra export script <workflow-id> -o flow.py        # workflow -> Python
nodyra workflow import flow.module.py --name "Restored flow"
```

- `--json` on any command (or `NODYRA_JSON=1`) → machine-readable output.
- `run start --watch` / `run watch <run-id>` follow a run live; exit code is
  `0` success, `2` error, `3` cancelled — script against it in CI.

### CI usage (no token file)

```bash
export NODYRA_BASE_URL=https://nodyra.example.com
export NODYRA_TOKEN=$NODYRA_CI_TOKEN            # an ndpat_ API token
nodyra --json run start "$WORKFLOW_ID" --watch
```

or non-interactive login: `echo "$TOKEN" | nodyra login --base-url $URL --token-stdin`.

## SDK quickstart

```python
from nodyra_client import NodyraClient

with NodyraClient(base_url="https://nodyra.example.com", token="ndpat_...") as c:
    wf = c.workflows.create(name="My automation")
    run = c.runs.start(wf.id, data={"key": "value"})
    for snapshot in c.runs.watch(run.id):
        print(snapshot.status)
```

All responses are typed pydantic models (`WorkflowDetail`, `RunDetail`, ...).
Errors raise `NodyraError(status, detail, hint)`.

## Configuration

| Source | Precedence |
|---|---|
| Explicit `NodyraClient(base_url=, token=)` / CLI flags | highest |
| `NODYRA_BASE_URL` / `NODYRA_TOKEN` env vars | middle |
| `~/.nodyra/token` (written by `nodyra login`, `0600`) | lowest |
````

Substitute `<owner>`. Verify `nodyra_client/__init__.py` actually exports `NodyraClient` (`grep -n "NodyraClient" packages/client/nodyra_client/__init__.py`); if not, add `from nodyra_client.client import NodyraClient, NodyraError` plus `__all__ = ["NodyraClient", "NodyraError"]` there so the README snippet is true.

- [ ] **Step 2:** Root `README.md`: add under the features/quickstart area:

```markdown
## CLI & Python SDK

`pip install nodyra-client` gives you the `nodyra` CLI (login, run workflows
from CI with `--watch` exit codes, import/export Python) and a typed SDK.
See [packages/client/README.md](packages/client/README.md).
```

- [ ] **Step 3: Verify README renders and examples are honest** — every command shown must exist: run `uv run nodyra --help` and each subcommand `--help`; fix drift.

- [ ] **Step 4: Commit**

```bash
git add packages/client/README.md README.md packages/client/nodyra_client/__init__.py
git commit -m "docs(cli): PyPI-grade README with CLI/SDK/CI quickstarts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Release lane — tag-driven PyPI publish

**Files:**
- Create: `.github/workflows/release-client.yml`

- [ ] **Step 1: Write the workflow:**

```yaml
name: Release nodyra-client

on:
  push:
    tags: ["client-v*"]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync --locked --all-packages
      - run: uv run ruff check packages/client
      - run: uv run pytest packages/client/tests -q

  publish:
    needs: test
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write   # PyPI trusted publishing — no API token stored
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - name: Guard - tag must match package version
        run: |
          TAG_VERSION="${GITHUB_REF_NAME#client-v}"
          PKG_VERSION=$(uv run --no-sync python -c "import tomllib;print(tomllib.load(open('packages/client/pyproject.toml','rb'))['project']['version'])")
          test "$TAG_VERSION" = "$PKG_VERSION" || { echo "tag $TAG_VERSION != pyproject $PKG_VERSION"; exit 1; }
      - run: uv build --package nodyra-client
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist/
```

- [ ] **Step 2: Sanity-check `_version.py` single-sourcing.** `nodyra_client/_version.py` holds `__version__`; it must equal pyproject's version. Add a test to `tests/test_cli.py`:

```python
def test_version_single_source() -> None:
    import tomllib
    from pathlib import Path

    from nodyra_client._version import __version__

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == __version__
```

Run it; if it fails, set `_version.py` to `0.2.0`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release-client.yml packages/client/tests/test_cli.py packages/client/nodyra_client/_version.py
git commit -m "ci: tag-driven PyPI release lane for nodyra-client (trusted publishing)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Manual prerequisite (record, don't attempt):** on PyPI, create the `nodyra-client` project pending publisher: PyPI → Your projects → Publishing → add GitHub `<owner>/nodyra`, workflow `release-client.yml`, environment `pypi`. Then releasing is: bump version in both files → `git tag client-v0.2.0 && git push --tags`.

---

### Task 9: Final verification

- [ ] **Step 1:** `uv run pytest packages/client/tests -q` → all pass (expect ≈25+ tests).
- [ ] **Step 2:** `uv run ruff check packages/client` → clean.
- [ ] **Step 3:** `uv build --package nodyra-client && uvx twine check dist/*` → PASSED ×2.
- [ ] **Step 4:** Manual smoke against a live dev stack (if running): `uv run nodyra login --base-url http://localhost:8000 --token <PAT>`, `uv run nodyra workflow list`, `uv run nodyra run start <id> --watch`. Record output in the PR description.
