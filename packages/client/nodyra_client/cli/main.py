"""nodyra CLI — command-line interface for the Nodyra workflow platform."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import click
import httpx

from nodyra_client._version import __version__
from nodyra_client.client import NodyraClient, NodyraError

# Token cache in user home directory.
_TOKEN_FILE = Path.home() / ".nodyra" / "token"


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
    return NodyraClient(base_url=base, token=token)


def _render(data: Any) -> None:
    """Render output as formatted JSON."""
    if data is None:
        click.echo("OK")
        return
    if isinstance(data, list):
        click.echo(json.dumps(
            [_simplify(item) for item in data], indent=2, default=str
        ))
    else:
        click.echo(json.dumps(_simplify(data), indent=2, default=str))


def _simplify(obj: Any) -> Any:
    """Convert Pydantic models and datetimes for JSON serialization."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _handle_error(exc: Exception) -> None:
    """Print a clean error message and exit 1."""
    click.echo(f"Error: {exc}", err=True)
    sys.exit(1)


@click.group()
@click.version_option(__version__, prog_name="nodyra")
def main() -> None:
    """nodyra — CLI for the Nodyra workflow automation platform.

    Set NODYRA_TOKEN and NODYRA_BASE_URL environment variables, or use
    'nodyra login' to save credentials.
    """


# ── Auth ──────────────────────────────────────────────────────────────

@main.command()
@click.option("--base-url", prompt=True, help="Nodyra instance URL")
@click.option("--token", prompt=True, hide_input=True, help="Bearer token")
def login(base_url: str, token: str) -> None:
    """Save credentials to ~/.nodyra/token."""
    # Create directory with restricted permissions (owner-only).
    _TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Atomically create the file with mode 0o600 so the token is never
    # visible to other users — no TOCTOU window between creation and chmod.
    payload = json.dumps({"base_url": base_url.rstrip("/"), "token": token}).encode()
    fd = os.open(str(_TOKEN_FILE), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
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
        _render(_client().whoami())
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
        _render(_client().workflows.list(status=status, search=search))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("get")
@click.argument("workflow_id")
def workflow_get(workflow_id: str) -> None:
    """Get workflow details."""
    try:
        _render(_client().workflows.get(workflow_id))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("create")
@click.option("--name", prompt=True, help="Workflow name")
@click.option("--folder-id", default=None, help="Folder ID")
def workflow_create(name: str, folder_id: str | None) -> None:
    """Create a new workflow."""
    try:
        _render(_client().workflows.create(name=name, folder_id=folder_id))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("delete")
@click.argument("workflow_id")
@click.confirmation_option(prompt="Are you sure you want to delete this workflow?")
def workflow_delete(workflow_id: str) -> None:
    """Delete a workflow."""
    try:
        _client().workflows.delete(workflow_id)
        click.echo("Deleted")
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@workflow.command("publish")
@click.argument("workflow_id")
def workflow_publish(workflow_id: str) -> None:
    """Publish a workflow version."""
    try:
        _render(_client().workflows.publish(workflow_id))
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
        _render(_client().runs.start(workflow_id, data=payload))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("get")
@click.argument("run_id")
def run_get(run_id: str) -> None:
    """Get run details."""
    try:
        _render(_client().runs.get(run_id))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("cancel")
@click.argument("run_id")
def run_cancel(run_id: str) -> None:
    """Cancel a running workflow."""
    try:
        _render(_client().runs.cancel(run_id))
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


@run.command("list")
@click.option("--workflow-id", default=None, help="Filter by workflow")
@click.option("--status", default=None, help="Filter: queued, running, success, error, cancelled")
def run_list(workflow_id: str | None, status: str | None) -> None:
    """List runs."""
    try:
        _render(_client().runs.list(workflow_id=workflow_id, status=status))
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
        _render(_client().credentials.list())
    except (NodyraError, httpx.TransportError) as exc:
        _handle_error(exc)


if __name__ == "__main__":
    main()
