"""Walk a new user's first run against a live stack, and fail if it breaks.

Every automated check in this repository exercises the code. None of them
installs the product and uses it. That gap let eleven bugs reach a release tag
with 9,828 tests passing — six of which made Nodyra unusable out of the box:

  * the shipped compose stack could not execute any workflow, because
    EXECUTION_SANDBOX=auto never fell back when no Docker socket was mounted
  * pre-flight refused runs whose packages were already installed
  * artifact downloads redirected to an address no browser can resolve
  * artifacts carried no checksum, while the UI offered to verify one

The existing ``compose-smoke`` CI job could not see any of it, for one reason:
it starts the stack with ``-f deploy/docker-compose.sandbox.yml``. That overlay
mounts a Docker socket, so CI always had a sandbox and never ran the topology
the getting-started guide actually documents. It then checked ``/health/ready``
and ``GET /templates`` and stopped — never running a workflow.

So this script drives the documented path, against the **base compose file
only**:

    register the first owner -> create from the starter template -> run it ->
    assert every node succeeded -> download the artifact -> verify its checksum

Exit code 0 means a new user can get to a working result. Anything else means
they cannot, and says which step failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 30


class SmokeFailure(RuntimeError):
    """A step of the first-run path did not work."""


def call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    raw: bool = False,
) -> object:
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise SmokeFailure(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{method} {path} unreachable: {exc.reason}") from None
    return payload if raw else (json.loads(payload) if payload else None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib following the artifact redirect automatically.

    The download endpoint answers 307 with a presigned S3 URL. urllib would
    follow it and carry the Authorization header along, so the object store sees
    both a bearer token and presigned query auth and refuses:

        InvalidRequest: request has multiple authentication types

    Browsers and curl strip the header when the redirect crosses origins, so
    this only bites programmatic clients. Follow it explicitly instead, without
    credentials.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


def fetch_artifact(base: str, artifact_id: str, token: str) -> bytes:
    """Download an artifact, following any presigned redirect unauthenticated."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        f"{base.rstrip('/')}/artifacts/{artifact_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with opener.open(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            detail = exc.read().decode(errors="replace")[:300]
            raise SmokeFailure(f"artifact download -> HTTP {exc.code}: {detail}") from None
        location = exc.headers.get("Location")
        if not location:
            raise SmokeFailure(f"artifact download returned {exc.code} with no Location") from None
        try:
            with urllib.request.urlopen(location, timeout=TIMEOUT) as signed:  # noqa: S310
                return signed.read()
        except urllib.error.URLError as inner:
            raise SmokeFailure(
                f"presigned artifact URL is not reachable ({inner.reason}). "
                f"A browser would fail the same way: {location.split('?')[0]}"
            ) from None


def step(number: int, message: str) -> None:
    print(f"[{number}] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", default="smoke-owner@nodyra.local")
    parser.add_argument("--password", default="FirstRun-Smoke-2026!")
    parser.add_argument(
        "--template",
        default="dataset_filter_export",
        help=(
            "id in the server catalogue (GET /templates). Note the web app also "
            "has its own gallery, WORKFLOW_TEMPLATES, whose ids differ — the "
            "activation checklist links to datasetref-filter-export there."
        ),
    )
    parser.add_argument("--run-timeout", type=float, default=300.0)
    args = parser.parse_args()
    base = args.base_url

    # 1. Readiness ---------------------------------------------------------
    step(1, "waiting for readiness")
    deadline = time.monotonic() + 120
    ready = None
    while time.monotonic() < deadline:
        try:
            ready = call(base, "/health/ready")
            break
        except SmokeFailure:
            time.sleep(2)
    if ready is None:
        raise SmokeFailure("/health/ready never returned 200")
    print(f"    {json.dumps(ready)}")

    # 2. The first account -------------------------------------------------
    step(2, "creating the first owner account")
    try:
        auth = call(
            base,
            "/auth/register",
            method="POST",
            body={"email": args.email, "password": args.password},
        )
    except SmokeFailure:
        # Re-runs against a warm stack: the owner already exists.
        auth = call(
            base,
            "/auth/login",
            method="POST",
            body={"email": args.email, "password": args.password},
        )
    token = auth["token"]  # type: ignore[index]
    role = (auth.get("user") or {}).get("role")  # type: ignore[union-attr]
    if role and role != "owner":
        raise SmokeFailure(f"first account has role {role!r}, expected 'owner'")
    print(f"    role={role or 'existing'}")

    # 3. The starter template ----------------------------------------------
    step(3, f"creating a workflow from {args.template!r}")
    templates = call(base, "/templates", token=token)
    catalogue = templates if isinstance(templates, list) else templates.get("items", [])  # type: ignore[union-attr]
    match = next((t for t in catalogue if t.get("id") == args.template), None)
    if match is None:
        available = sorted(t.get("id", "?") for t in catalogue)
        raise SmokeFailure(
            f"template {args.template!r} is not in the catalogue. The activation "
            f"checklist links to it, so it must ship. Available: {available}"
        )
    # Use the product's own instantiate endpoint rather than reassembling the
    # graph here — that is what the New-workflow dialog calls, so this exercises
    # the same path a user does instead of a parallel one that could drift.
    created = call(
        base,
        f"/templates/{args.template}/instantiate",
        method="POST",
        body={"name": "first-run smoke"},
        token=token,
    )
    workflow_id = created["id"] if "id" in created else created["workflow_id"]  # type: ignore[operator,index]
    detail = call(base, f"/workflows/{workflow_id}", token=token)
    node_count = len(((detail or {}).get("graph") or {}).get("nodes", []))  # type: ignore[union-attr]
    if node_count == 0:
        raise SmokeFailure("instantiated workflow has an empty graph")
    print(f"    workflow {workflow_id} with {node_count} nodes")

    # 4. Run it ------------------------------------------------------------
    step(4, "running it")
    started = call(base, f"/workflows/{workflow_id}/run", method="POST", body={}, token=token)
    run_id = started["run_id"]  # type: ignore[index]
    deadline = time.monotonic() + args.run_timeout
    status = "queued"
    while time.monotonic() < deadline:
        run = call(base, f"/runs/{run_id}", token=token)
        status = str(run.get("status"))  # type: ignore[union-attr]
        if status in {"success", "succeeded", "failed", "error", "cancelled"}:
            break
        time.sleep(2)
    if status not in {"success", "succeeded"}:
        run = call(base, f"/runs/{run_id}", token=token)
        raise SmokeFailure(
            f"run finished as {status!r}: {str(run.get('error'))[:400]}"  # type: ignore[union-attr]
        )
    print(f"    run {run_id} -> {status}")

    # 5. The artifact, and its integrity -----------------------------------
    step(5, "downloading the artifact and verifying its checksum")
    listing = call(base, f"/artifacts?run_id={run_id}", token=token)
    artifacts = listing if isinstance(listing, list) else listing.get("items", [])  # type: ignore[union-attr]
    if not artifacts:
        raise SmokeFailure("the run produced no artifacts")

    verified = 0
    for artifact in artifacts:
        claimed = artifact.get("checksum_sha256")
        if not claimed:
            raise SmokeFailure(
                f"artifact {artifact.get('name')!r} has no checksum; the activation "
                f"checklist offers to verify one"
            )
        blob = fetch_artifact(base, artifact["id"], token)
        actual = hashlib.sha256(blob).hexdigest()
        if actual != claimed:
            raise SmokeFailure(
                f"artifact {artifact.get('name')!r} failed integrity: "
                f"claimed {claimed[:16]}..., got {actual[:16]}... "
                f"({len(blob)} bytes downloaded)"  # type: ignore[arg-type]
            )
        verified += 1
    print(f"    {verified} artifact(s) downloaded and verified")

    print("\nOK: a new user can install, create, run, and retrieve a verified result.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"\nFIRST-RUN FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
