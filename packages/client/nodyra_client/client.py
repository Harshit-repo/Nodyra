"""Typed HTTP client for the Nodyra API."""

from __future__ import annotations

import os
from typing import Any

import httpx

from nodyra_client._version import __version__
from nodyra_client.models import (
    CredentialSummary,
    RunCreated,
    RunDetail,
    RunSummary,
    WorkflowDetail,
    WorkflowGraph,
    WorkflowSummary,
)


class NodyraError(Exception):
    """Raised when the API returns a non-2xx status."""

    def __init__(self, status: int, detail: str, hint: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.hint = hint
        super().__init__(f"[{status}] {detail}")


def _env_token() -> str | None:
    return os.environ.get("NODYRA_TOKEN")


def _env_base() -> str:
    return os.environ.get("NODYRA_BASE_URL", "http://localhost:8000")


class NodyraClient:
    """Typed HTTP client for the Nodyra workflow automation API.

    Usage as context manager (recommended)::

        with NodyraClient(base_url="https://nodyra.example.com", token="...") as c:
            wf = c.workflows.create(name="My Automation")
            run = c.runs.start(wf.id, data={"key": "value"})

    Or with explicit close::

        client = NodyraClient(base_url="https://nodyra.example.com", token="...")
        try:
            wf = client.workflows.create(name="My Automation")
        finally:
            client.close()
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = (base_url or _env_base()).strip().rstrip("/")
        self._token = token or _env_token()
        self._timeout = max(1.0, float(timeout))
        self._client = httpx.Client(
            base_url=self._base,
            timeout=self._timeout,
            headers=self._headers(),
            transport=httpx.HTTPTransport(retries=2),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> NodyraClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "User-Agent": f"nodyra-client/{__version__}",
            "Accept": "application/json",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get(self, path: str, **params: Any) -> Any:
        r = self._client.get(path, params=params)
        return self._check(r)

    def _post(self, path: str, json: dict | None = None) -> Any:
        r = self._client.post(path, json=json)
        return self._check(r)

    def _put(self, path: str, json: dict | None = None) -> Any:
        r = self._client.put(path, json=json)
        return self._check(r)

    def _delete(self, path: str) -> None:
        r = self._client.delete(path)
        self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> Any:
        if r.is_success:
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        detail = "Unknown error"
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text or f"HTTP {r.status_code}"
        hint = None
        if r.status_code == 401:
            hint = "Authentication failed - run 'nodyra login' or set NODYRA_TOKEN."
        elif r.status_code == 403:
            hint = (
                "Permission denied - the token lacks a required scope or role. "
                "Create a token with the needed scopes in Settings > API tokens."
            )
        raise NodyraError(r.status_code, detail, hint)

    # ── Auth ─────────────────────────────────────────────────────────────

    def whoami(self) -> dict[str, Any]:
        """Return current user info."""
        return self._get("/auth/me")

    # ── Workflows ────────────────────────────────────────────────────────

    @property
    def workflows(self) -> _WorkflowsAPI:
        return _WorkflowsAPI(self)

    # ── Runs ─────────────────────────────────────────────────────────────

    @property
    def runs(self) -> _RunsAPI:
        return _RunsAPI(self)

    # ── Credentials ──────────────────────────────────────────────────────

    @property
    def credentials(self) -> _CredentialsAPI:
        return _CredentialsAPI(self)

    # ── Export ───────────────────────────────────────────────────────────

    @property
    def export_(self) -> _ExportAPI:
        return _ExportAPI(self)


class _WorkflowsAPI:
    def __init__(self, client: NodyraClient) -> None:
        self._c = client

    def list(
        self,
        *,
        status: str | None = None,
        folder_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowSummary]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if folder_id:
            params["folder_id"] = folder_id
        if search:
            params["search"] = search
        data = self._c._get("/workflows", **params)
        return [WorkflowSummary(**item) for item in data]

    def get(self, workflow_id: str) -> WorkflowDetail:
        return WorkflowDetail(**self._c._get(f"/workflows/{workflow_id}"))

    def create(self, *, name: str, folder_id: str | None = None) -> WorkflowDetail:
        body: dict[str, Any] = {"name": name}
        if folder_id:
            body["folder_id"] = folder_id
        return WorkflowDetail(**self._c._post("/workflows", body))

    def update(
        self,
        workflow_id: str,
        *,
        name: str | None = None,
        graph: WorkflowGraph | None = None,
        environment_id: str | None = None,
    ) -> WorkflowDetail:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if graph is not None:
            body["draft_graph"] = graph.model_dump()
        if environment_id is not None:
            body["environment_id"] = environment_id
        return WorkflowDetail(**self._c._put(f"/workflows/{workflow_id}", body))

    def delete(self, workflow_id: str) -> None:
        self._c._delete(f"/workflows/{workflow_id}")

    def publish(self, workflow_id: str) -> dict[str, Any]:
        return self._c._post(f"/workflows/{workflow_id}/publish")


class _RunsAPI:
    def __init__(self, client: NodyraClient) -> None:
        self._c = client

    def start(
        self,
        workflow_id: str,
        *,
        data: dict[str, Any] | None = None,
        mode: str = "manual",
    ) -> RunCreated:
        body: dict[str, Any] = {"mode": mode}
        if data:
            body["parameters"] = data
        return RunCreated(**self._c._post(f"/workflows/{workflow_id}/run", body))

    def get(self, run_id: str) -> RunDetail:
        return RunDetail(**self._c._get(f"/runs/{run_id}"))

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._c._post(f"/runs/{run_id}/cancel")

    def list(
        self,
        *,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunSummary]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if workflow_id:
            params["workflow_id"] = workflow_id
        if status:
            params["status"] = status
        data = self._c._get("/runs", **params)
        return [RunSummary(**item) for item in data]


class _CredentialsAPI:
    def __init__(self, client: NodyraClient) -> None:
        self._c = client

    def list(self) -> list[CredentialSummary]:
        data = self._c._get("/credentials")
        return [CredentialSummary(**item) for item in data]

    def create(self, *, name: str, type: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._c._post("/credentials", {"name": name, "type": type, "data": data})


class _ExportAPI:
    def __init__(self, client: NodyraClient) -> None:
        self._c = client

    def as_script(self, workflow_id: str) -> str:
        r = self._c._client.get(
            f"/workflows/{workflow_id}/export.py",
            headers=self._c._headers(),
        )
        self._c._check(r)
        return r.text

    def as_module(self, workflow_id: str) -> str:
        r = self._c._client.get(
            f"/workflows/{workflow_id}/export.module.py",
            headers=self._c._headers(),
        )
        self._c._check(r)
        return r.text
