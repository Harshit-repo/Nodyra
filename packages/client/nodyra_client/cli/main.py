"""nodyra CLI — command-line interface for the Nodyra workflow platform."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from nodyra_client._version import __version__
from nodyra_client.client import NodyraClient, NodyraError

# Token cache in user home directory.
_TOKEN_FILE = Path.home() / ".nodyra" / "token"


def _client() -> NodyraClient:
    """Build a client from env vars or saved token."""
    base = None
    token = None
    if _TOKEN_FILE.exists():
        data = json.loads(_TOKEN_FILE.read_text())
        base = data.get("base_url")
        token = data.get("token")
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
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps({"base_url": base_url.rstrip("/"), "token": token}))
    # Restrict permissions so only the owner can read the token.
    # chmod is a no-op on Windows; the directory permissions provide privacy there.
    try:
        _TOKEN_FILE.chmod(0o600)
    except (OSError, NotImplementedError):
        pass
    client = NodyraClient(base_url=base_url, token=token)
    try:
        user = client.whoami()
        click.echo(f"Authenticated as {user.get('email', 'unknown')}")
    except NodyraError as exc:
        click.echo(f"Warning: token saved but /auth/me failed: {exc}", err=True)


@main.command()
def whoami() -> None:
    """Show the currently authenticated user."""
    try:
        _render(_client().whoami())
    except NodyraError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


# ── Workflows ──────────────────────────────────────────────────────────

@main.group()
def workflow() -> None:
    """Manage workflows."""


@workflow.command("list")
@click.option("--status", default=None, help="Filter: draft, published")
@click.option("--search", default=None, help="Search by name")
def workflow_list(status: str | None, search: str | None) -> None:
    """List workflows."""
    _render(_client().workflows.list(status=status, search=search))


@workflow.command("get")
@click.argument("workflow_id")
def workflow_get(workflow_id: str) -> None:
    """Get workflow details."""
    _render(_client().workflows.get(workflow_id))


@workflow.command("create")
@click.option("--name", prompt=True, help="Workflow name")
@click.option("--folder-id", default=None, help="Folder ID")
def workflow_create(name: str, folder_id: str | None) -> None:
    """Create a new workflow."""
    wf = _client().workflows.create(name=name, folder_id=folder_id)
    _render(wf)


@workflow.command("delete")
@click.argument("workflow_id")
@click.confirmation_option(prompt="Are you sure you want to delete this workflow?")
def workflow_delete(workflow_id: str) -> None:
    """Delete a workflow."""
    _client().workflows.delete(workflow_id)
    click.echo("Deleted")


@workflow.command("publish")
@click.argument("workflow_id")
def workflow_publish(workflow_id: str) -> None:
    """Publish a workflow version."""
    _render(_client().workflows.publish(workflow_id))


# ── Runs ───────────────────────────────────────────────────────────────

@main.group()
def run() -> None:
    """Manage workflow runs."""


@run.command("start")
@click.argument("workflow_id")
@click.option("--data", default=None, help="JSON input data for the run")
def run_start(workflow_id: str, data: str | None) -> None:
    """Start a workflow run."""
    payload = json.loads(data) if data else None
    _render(_client().runs.start(workflow_id, data=payload))


@run.command("get")
@click.argument("run_id")
def run_get(run_id: str) -> None:
    """Get run details."""
    _render(_client().runs.get(run_id))


@run.command("cancel")
@click.argument("run_id")
def run_cancel(run_id: str) -> None:
    """Cancel a running workflow."""
    _render(_client().runs.cancel(run_id))


@run.command("list")
@click.option("--workflow-id", default=None, help="Filter by workflow")
@click.option("--status", default=None, help="Filter: queued, running, success, error, cancelled")
def run_list(workflow_id: str | None, status: str | None) -> None:
    """List runs."""
    _render(_client().runs.list(workflow_id=workflow_id, status=status))


# ── Export ─────────────────────────────────────────────────────────────

@main.group()
def export() -> None:
    """Export workflows."""


@export.command("script")
@click.argument("workflow_id")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def export_script(workflow_id: str, output: str | None) -> None:
    """Export a workflow as a standalone Python script."""
    code = _client().export_.as_script(workflow_id)
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
    code = _client().export_.as_module(workflow_id)
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
    _render(_client().credentials.list())


if __name__ == "__main__":
    main()
