"""nodyra CLI — command-line interface for the Nodyra workflow platform."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import click
import httpx

from nodyra_client._version import __version__
from nodyra_client.cli.render import emit
from nodyra_client.client import NodyraClient, NodyraError

# Token cache in user home directory.
_TOKEN_FILE = Path.home() / ".nodyra" / "token"
_WF_COLUMNS = [
    ("ID", "id"),
    ("Name", "name"),
    ("Status", "status"),
    ("Version", "latest_version"),
    ("Updated", "updated_at"),
]
_RUN_COLUMNS = [
    ("ID", "id"),
    ("Workflow", "workflow_name"),
    ("Status", "status"),
    ("Mode", "mode"),
    ("Started", "started_at"),
    ("Duration (s)", "duration_seconds"),
]
_CRED_COLUMNS = [("ID", "id"), ("Name", "name"), ("Type", "type"), ("Scope", "scope")]


def _write_token_file(payload: bytes) -> None:
    """Atomically replace the token file with owner-only permissions."""
    token_dir = _TOKEN_FILE.parent
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(token_dir, 0o700)
    except OSError:
        pass

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{_TOKEN_FILE.name}.",
        suffix=".tmp",
        dir=token_dir,
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, _TOKEN_FILE)
        try:
            os.chmod(_TOKEN_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def _client() -> NodyraClient:
    """Build a client from env vars or saved token."""
    base = None
    token = None
    if _TOKEN_FILE.exists():
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            base = data.get("base_url")
            token = data.get("token")
        except (json.JSONDecodeError, OSError):
            click.echo(
                "Error: credential file is corrupt. Run 'nodyra login' to re-authenticate.",
                err=True,
            )
            sys.exit(1)
    base = os.environ.get("NODYRA_BASE_URL", base)
    token = os.environ.get("NODYRA_TOKEN", token)
    return NodyraClient(base_url=base, token=token)


def _json_mode() -> bool:
    ctx = click.get_current_context(silent=True)
    while ctx is not None:
        if isinstance(ctx.obj, dict) and "json" in ctx.obj:
            return bool(ctx.obj["json"])
        ctx = ctx.parent
    return False


def _handle_error(exc: Exception) -> None:
    """Print a clean error message and exit 1."""
    click.echo(f"Error: {exc}", err=True)
    hint = getattr(exc, "hint", None)
    if hint:
        click.echo(f"Hint: {hint}", err=True)
    sys.exit(1)


@click.group()
@click.version_option(__version__, prog_name="nodyra")
@click.option(
    "--json",
    "json_mode",
    is_flag=True,
    envvar="NODYRA_JSON",
    help="Machine-readable JSON output.",
)
@click.pass_context
def main(ctx: click.Context, json_mode: bool) -> None:
    """nodyra — CLI for the Nodyra workflow automation platform.

    Set NODYRA_TOKEN and NODYRA_BASE_URL environment variables, or use
    'nodyra login' to save credentials.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode


# ── Auth ──────────────────────────────────────────────────────────────

@main.command()
@click.option("--base-url", prompt=True, help="Nodyra instance URL")
@click.option("--token", prompt=True, hide_input=True, help="Bearer token")
def login(base_url: str, token: str) -> None:
    """Save credentials to ~/.nodyra/token."""
    # Atomically replace the file with mode 0o600 so an existing file or
    # symlink cannot be followed and truncated.
    payload = json.dumps({"base_url": base_url.rstrip("/"), "token": token}).encode()
    _write_token_file(payload)
    # Verify the token — use context manager to ensure the connection pool
    # is closed even if whoami() raises.
    with NodyraClient(base_url=base_url, token=token) as client:
        try:
            user = client.whoami()
            click.echo(f"Authenticated as {user.get('email', 'unknown')}")
        except (NodyraError, httpx.TransportError) as exc:
            click.echo(f"Warning: token saved but /auth/me failed: {exc}", err=True)


@main.command()
def whoami() -> None:
    """Show the currently authenticated user."""
    try:
        emit(_client().whoami(), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


# ── Workflows ──────────────────────────────────────────────────────────

@main.group()
def workflow() -> None:
    """Manage workflows."""


@workflow.command("list")
@click.option("--status", default=None, help="Filter: draft, published")
@click.option("--search", default=None, help="Search by name")
def workflow_list(status: str | None, search: str | None) -> None:
    """List workflows."""
    try:
        emit(
            _client().workflows.list(status=status, search=search),
            json_mode=_json_mode(),
            columns=_WF_COLUMNS,
        )
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("get")
@click.argument("workflow_id")
def workflow_get(workflow_id: str) -> None:
    """Get workflow details."""
    try:
        emit(_client().workflows.get(workflow_id), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("create")
@click.option("--name", prompt=True, help="Workflow name")
@click.option("--folder-id", default=None, help="Folder ID")
def workflow_create(name: str, folder_id: str | None) -> None:
    """Create a new workflow."""
    try:
        emit(_client().workflows.create(name=name, folder_id=folder_id), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("delete")
@click.argument("workflow_id")
@click.confirmation_option(prompt="Are you sure you want to delete this workflow?")
def workflow_delete(workflow_id: str) -> None:
    """Delete a workflow."""
    try:
        _client().workflows.delete(workflow_id)
        emit(None, json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("publish")
@click.argument("workflow_id")
def workflow_publish(workflow_id: str) -> None:
    """Publish a workflow version."""
    try:
        emit(_client().workflows.publish(workflow_id), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


# ── Runs ───────────────────────────────────────────────────────────────

@main.group()
def run() -> None:
    """Manage workflow runs."""


@run.command("start")
@click.argument("workflow_id")
@click.option("--data", default=None, help="JSON input data for the run")
def run_start(workflow_id: str, data: str | None) -> None:
    """Start a workflow run."""
    payload: dict | None = None
    if data:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            click.echo(f"Error: --data must be valid JSON: {exc}", err=True)
            sys.exit(1)
    try:
        emit(_client().runs.start(workflow_id, data=payload), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("get")
@click.argument("run_id")
def run_get(run_id: str) -> None:
    """Get run details."""
    try:
        emit(_client().runs.get(run_id), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("cancel")
@click.argument("run_id")
def run_cancel(run_id: str) -> None:
    """Cancel a running workflow."""
    try:
        emit(_client().runs.cancel(run_id), json_mode=_json_mode())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("list")
@click.option("--workflow-id", default=None, help="Filter by workflow")
@click.option("--status", default=None, help="Filter: queued, running, success, error, cancelled")
def run_list(workflow_id: str | None, status: str | None) -> None:
    """List runs."""
    try:
        emit(
            _client().runs.list(workflow_id=workflow_id, status=status),
            json_mode=_json_mode(),
            columns=_RUN_COLUMNS,
        )
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


# ── Export ─────────────────────────────────────────────────────────────

@main.group()
def export() -> None:
    """Export workflows."""


@export.command("script")
@click.argument("workflow_id")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def export_script(workflow_id: str, output: str | None) -> None:
    """Export a workflow as a standalone Python script."""
    try:
        code = _client().export_.as_script(workflow_id)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
        return
    if output:
        Path(output).write_text(code)
        click.echo(f"Written to {output}")
    else:
        click.echo(code)


@export.command("module")
@click.argument("workflow_id")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def export_module(workflow_id: str, output: str | None) -> None:
    """Export a workflow as a re-importable Python module."""
    try:
        code = _client().export_.as_module(workflow_id)
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)
        return
    if output:
        Path(output).write_text(code)
        click.echo(f"Written to {output}")
    else:
        click.echo(code)


# ── Credentials ────────────────────────────────────────────────────────

@main.group()
def credential() -> None:
    """Manage credentials."""


@credential.command("list")
def credential_list() -> None:
    """List credentials."""
    try:
        emit(
            _client().credentials.list(),
            json_mode=_json_mode(),
            columns=_CRED_COLUMNS,
        )
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


if __name__ == "__main__":
    main()
