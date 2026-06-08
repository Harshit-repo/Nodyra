"""Trigger dispatch.

Webhook ingress runs workflows whose graph contains a matching webhook node,
and an in-process scheduler fires schedule-trigger workflows. Schedules support
both a simple interval (every N minutes/hours/days) and full cron expressions.
``last_fired`` is persisted in the DB so the scheduler survives a restart
without missing or double-firing. A multi-replica deployment can disable this
loop (``enable_inprocess_scheduler=false``) and drive runs from Celery Beat.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Deployment,
    ProviderTriggerSubscription,
    Run,
    ScheduleState,
    Workflow,
    WorkflowVersion,
)
from app.services.graph_utils import first_trigger_node
from app.services.runner import start_run

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}

# Don't log the same unknown-timezone string every tick — flood control.
_logged_bad_tz: set[str] = set()


def _resolve_tz(name: str) -> ZoneInfo | None:
    """Return the IANA zone for ``name``, or ``None`` if it's unknown.

    Returning ``None`` lets callers decide what "invalid" means in their
    context — for ``_is_due`` it means "skip this tick" (don't fall back to
    UTC, which would silently fire crons at the wrong absolute time).
    Blank/unset is *not* invalid — it's interpreted as UTC.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError:
        return None


def _is_due(params: dict, last: datetime, now: datetime) -> bool:
    """Return True if a schedule with these params is due relative to ``last``."""
    # SQLite returns naive datetimes; treat a stored value as UTC so it can be
    # compared against the timezone-aware ``now``.
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    cron = str(params.get("cron", "") or "").strip()
    if cron:
        # Evaluate the cron in the user-selected timezone so an expression like
        # "0 9 * * *" really means 09:00 *local* (not 09:00 UTC). croniter
        # respects the tzinfo of the base datetime. Resolution chain:
        # node-level ``tz`` → app-wide default (settings.app_timezone) → UTC.
        raw_tz_name = (
            str(params.get("tz", "") or "").strip() or settings.app_timezone
        )
        tz = _resolve_tz(raw_tz_name)
        if tz is None:
            # Unknown IANA name — refuse to fire rather than silently use UTC.
            # Log once per name so the loop stays quiet.
            if raw_tz_name not in _logged_bad_tz:
                _logged_bad_tz.add(raw_tz_name)
                logger.warning(
                    "schedule trigger has unknown timezone %r; not firing",
                    raw_tz_name,
                )
            return False
        last_local = last.astimezone(tz)
        try:
            next_time = croniter(cron, last_local).get_next(datetime)
            return next_time <= now
        except (ValueError, KeyError):
            return False  # malformed cron — never fire rather than crash
    interval = params.get("interval", "hours")
    every = max(int(params.get("every", 1) or 1), 1)
    period = _INTERVAL_SECONDS.get(interval, 3600) * every
    return (now - last).total_seconds() >= period


async def _active_workflows() -> list[Workflow]:
    async with SessionLocal() as session:
        result = await session.scalars(
            select(Workflow)
            .where(Workflow.active.is_(True))
            .options(selectinload(Workflow.versions))
        )
        return list(result.all())


async def _resolve_node_auth(
    params: dict,
    workflow_id: str,
    environment_id: str | None,
    session=None,
) -> dict:
    """Pre-resolve credential refs inside the webhook node's auth params.

    Returns a copy of ``params`` with any ``{"__noodle_credential__": True}``
    references replaced by their decrypted values. Auth comparison happens
    in :func:`dispatch_webhook` against the resolved strings, never against
    the stored references.

    Pass a shared ``session`` to avoid opening a new DB connection per node
    when called in a loop (e.g. during webhook dispatch).
    """
    from app.services.credentials import resolve_credential_refs

    # New workflows store the entire auth config inside auth_credentials.
    # Legacy workflows pinned individual credential references per field;
    # keep those keys so old graphs continue to validate.
    auth_keys = (
        "auth_credentials",
        "auth_username",
        "auth_password",
        "auth_header_value",
        "auth_query_value",
        "auth_bearer_token",
        "auth_jwt_secret",
        "hmac_secret",
    )
    snapshot = {key: params.get(key) for key in auth_keys}
    if session is not None:
        return await resolve_credential_refs(
            session,
            snapshot,
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
    async with SessionLocal() as _session:
        return await resolve_credential_refs(
            _session,
            snapshot,
            workflow_id=workflow_id,
            environment_id=environment_id,
        )


def _matches_basic_auth(
    auth_header: str | None, expected_user: str, expected_pass: str
) -> bool:
    if not auth_header or not auth_header.lower().startswith("basic "):
        return False
    import base64

    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, _, password = decoded.partition(":")
    return user == expected_user and password == expected_pass


def _webhook_auth_passes(
    node_params: dict, resolved: dict, headers: dict, query: dict
) -> bool:
    """Return True if the incoming request satisfies the node's auth_type."""
    auth_type = str(node_params.get("auth_type") or "none").lower()
    if auth_type == "none":
        return True
    # Header keys arrive lower-cased from FastAPI's CIMultiDict; normalise.
    lower_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth_credentials = resolved.get("auth_credentials")
    creds = auth_credentials if isinstance(auth_credentials, dict) else None

    if auth_type == "basic":
        if creds:
            username = str(creds.get("username") or "")
            password = str(creds.get("password") or "")
        else:
            # Legacy: per-field credential references on old workflows.
            username = str(resolved.get("auth_username") or "")
            password = str(resolved.get("auth_password") or "")
        return _matches_basic_auth(lower_headers.get("authorization"), username, password)

    if auth_type == "header":
        if creds:
            name = str(creds.get("name") or "X-API-Key").lower()
            expected = str(creds.get("value") or "")
        else:
            # Legacy fallback for workflows that pinned name on the node.
            name = str(node_params.get("auth_header_name") or "X-API-Key").lower()
            expected = str(resolved.get("auth_header_value") or "")
        return bool(expected) and lower_headers.get(name) == expected

    if auth_type == "query":
        if creds:
            name = str(creds.get("name") or "token")
            expected = str(creds.get("value") or "")
        else:
            name = str(node_params.get("auth_query_name") or "token")
            expected = str(resolved.get("auth_query_value") or "")
        return bool(expected) and str((query or {}).get(name) or "") == expected

    if auth_type == "bearer":
        import hmac

        if creds:
            expected = str(creds.get("token") or creds.get("value") or "")
        else:
            expected = str(resolved.get("auth_bearer_token") or "")
        got = lower_headers.get("authorization", "")
        if not got.lower().startswith("bearer "):
            return False
        return bool(expected) and hmac.compare_digest(got[7:].strip(), expected)

    if auth_type == "jwt":
        if creds:
            secret = str(creds.get("secret") or creds.get("value") or "")
        else:
            secret = str(resolved.get("auth_jwt_secret") or "")
        header_name = str(node_params.get("auth_jwt_header") or "authorization").lower()
        token = lower_headers.get(header_name, "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return bool(secret) and _jwt_hs256_valid(token, secret)

    return False


def _b64url_decode(segment: str) -> bytes:
    import base64

    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _jwt_hs256_valid(token: str, secret: str) -> bool:
    """Verify a compact HS256 JWT: signature + alg + ``exp``.

    Intentionally stdlib-only (no PyJWT dependency) and HS256-only — the common
    shared-secret webhook case. RS256/asymmetric verification is a follow-up
    that would need a JWT/crypto library.
    """
    import hashlib
    import hmac
    import json as _json
    import time

    parts = token.split(".")
    if len(parts) != 3:
        return False
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        got_sig = _b64url_decode(sig_b64)
    except Exception:
        return False
    if not hmac.compare_digest(got_sig, expected_sig):
        return False
    try:
        header = _json.loads(_b64url_decode(header_b64))
        payload = _json.loads(_b64url_decode(payload_b64))
    except Exception:
        return False
    if str(header.get("alg")) != "HS256":
        return False
    exp = payload.get("exp")
    if exp is not None:
        try:
            if float(exp) < time.time():
                return False
        except (TypeError, ValueError):
            return False
    return True


def _webhook_hmac_passes(
    node_params: dict, resolved: dict, headers: dict, raw_body: bytes | None
) -> bool:
    """Verify an HMAC signature header over the raw request body.

    GitHub/Stripe/Slack style: ``<prefix><hexdigest>`` in a configurable header,
    computed with a shared secret. Off unless ``hmac_verification == "on"``.
    """
    import hashlib
    import hmac

    if str(node_params.get("hmac_verification") or "off").lower() != "on":
        return True
    creds = resolved.get("auth_credentials")
    creds = creds if isinstance(creds, dict) else None
    secret = str((creds or {}).get("hmac_secret") or resolved.get("hmac_secret") or "")
    if not secret:
        return False
    header_name = str(node_params.get("hmac_header") or "X-Signature").lower()
    algo = str(node_params.get("hmac_algorithm") or "sha256").lower()
    digestmod = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}.get(algo)
    if digestmod is None:
        return False
    prefix = str(node_params.get("hmac_prefix") or "")
    lower_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    provided = lower_headers.get(header_name, "")
    if prefix and provided.startswith(prefix):
        provided = provided[len(prefix):]
    expected = hmac.new(secret.encode(), raw_body or b"", digestmod).hexdigest()
    return hmac.compare_digest(provided.strip(), expected)


def _webhook_ip_allowed(
    node_params: dict, client_ip: str | None, headers: dict
) -> bool:
    """Return True if ``client_ip`` is permitted by the node's ip_allowlist.

    An empty allowlist permits everyone. A non-empty allowlist with no parseable
    CIDR/IP entries fails closed (the operator intended to restrict). When
    ``trust_proxy`` is on, the left-most ``X-Forwarded-For`` entry is used as the
    caller IP; otherwise the socket peer is authoritative (XFF is spoofable).
    """
    import ipaddress
    import re

    raw = str(node_params.get("ip_allowlist") or "").strip()
    if not raw:
        return True
    nets: list = []
    for token in re.split(r"[,\n]", raw):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    if not nets:
        return False  # configured but unparseable → fail closed

    candidate = client_ip
    if str(node_params.get("trust_proxy") or "off").lower() == "on":
        lower_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        xff = lower_headers.get("x-forwarded-for")
        if xff:
            candidate = xff.split(",")[0].strip()
    if not candidate:
        return False
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(ip in net for net in nets)


def _webhook_dedup_key(node_params: dict, request_payload: dict) -> str | None:
    """Evaluate the node's ``dedup_key`` expression against the request payload.

    Returns the stringified key, or ``None`` when dedup is off, no key is
    configured, or the expression yields nothing — in which case the caller
    runs the workflow normally (there's nothing to deduplicate on).
    """
    if str(node_params.get("dedup") or "off").lower() != "on":
        return None
    expr = node_params.get("dedup_key")
    if not expr:
        return None
    from noodle.expr import build_context, evaluate

    try:
        value = evaluate(expr, build_context(first_input=request_payload))
    except Exception:  # noqa: BLE001 - a bad expression must not 500 the webhook
        return None
    if value is None or isinstance(value, str) and value.startswith("[expr error"):
        return None
    key = str(value).strip()
    return key or None


def _webhook_on_received_response(
    node_params: dict, request_payload: dict
) -> dict | None:
    """Shape the immediate ``On Received`` response for a webhook node.

    Returns ``None`` to use the default JSON ack (``response_data`` unset or the
    node isn't in On Received mode). Otherwise returns
    ``{"status", "headers", "body", "no_body"}`` describing the response to send
    back synchronously, before the dispatched run completes. ``First Entry`` /
    ``All Entries`` operate on the received request body; ``Custom`` evaluates
    ``response_body`` / ``response_headers`` against the request (``$json``).
    """
    mode = str(node_params.get("response_mode") or "On Received")
    if mode != "On Received":
        return None
    data_mode = str(node_params.get("response_data") or "").strip()
    if not data_mode:
        return None
    try:
        code = int(node_params.get("response_code") or 200)
    except (TypeError, ValueError):
        code = 200
    body_in = (request_payload or {}).get("body")
    if data_mode == "No Body":
        return {"status": code, "headers": {}, "body": None, "no_body": True}
    if data_mode == "All Entries":
        return {"status": code, "headers": {}, "body": body_in, "no_body": False}
    if data_mode == "First Entry JSON":
        first = body_in[0] if isinstance(body_in, list) and body_in else body_in
        return {"status": code, "headers": {}, "body": first, "no_body": False}
    if data_mode == "Custom":
        from noodle.expr import build_context, evaluate

        ctx = build_context(first_input=request_payload)
        body = evaluate(node_params.get("response_body") or "", ctx)
        headers_spec = node_params.get("response_headers") or {}
        headers: dict[str, str] = {}
        if isinstance(headers_spec, dict):
            headers = {
                str(k): str(evaluate(v, ctx)) for k, v in headers_spec.items()
            }
        return {"status": code, "headers": headers, "body": body, "no_body": False}
    return None


def _capture_raw_body_artifact(
    raw_body: bytes, headers: dict, run_id: str, node_id: str
) -> dict | None:
    """Write the raw request bytes as an artifact for ``run_id``.

    Returns the artifact ref, or ``None`` if there are no bytes or the write
    fails (e.g. over ``max_artifact_bytes``) — capture is best-effort and must
    never fail the webhook itself. Bytes are written via the run-scoped
    ``LocalArtifactStore`` so they're cleaned up with the run and never bloat
    the DB; the caller persists the row (and rehomes to the configured backend)
    via ``persist_artifact_refs``.
    """
    import mimetypes

    from noodle.artifacts import LocalArtifactStore
    from noodle.context import current_node_id

    if not raw_body:
        return None
    lower_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    content_type = (
        lower_headers.get("content-type", "").split(";")[0].strip()
        or "application/octet-stream"
    )
    ext = mimetypes.guess_extension(content_type) or ".bin"
    store = LocalArtifactStore(
        settings.artifacts_dir,
        run_id,
        max_bytes=settings.max_artifact_bytes,
        max_count=settings.max_artifacts_per_run,
    )
    token = current_node_id.set(node_id)
    try:
        return store.write_bytes(
            raw_body,
            name=f"webhook-body{ext}",
            content_type=content_type,
            kind="binary",
        )
    except Exception:  # noqa: BLE001 - capture is best-effort
        logger.warning("webhook raw-body capture failed", exc_info=True)
        return None
    finally:
        current_node_id.reset(token)


def _int_or(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


_TERMINAL_RUN_STATES = {"success", "error", "cancelled"}


async def _await_run_terminal(run_id: str, timeout: float) -> str | None:
    """Wait until ``run_id`` reaches a terminal state, or ``timeout`` elapses.

    Hybrid: subscribes to the run broker (low latency when the broker is live,
    incl. cross-replica over Redis) AND polls the shared DB every ~250ms (the
    authoritative source, and the safety net if a broker event is missed or the
    run executes on another replica without a shared broker). Returns the
    terminal status, or ``None`` on timeout.
    """
    import contextlib

    from app.services.events import broker

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)

    async def _broker_finished() -> None:
        with contextlib.suppress(Exception):
            async for event in broker.subscribe(run_id):
                if event.get("type") == "run_finished":
                    return

    broker_task = asyncio.create_task(_broker_finished())
    try:
        while True:
            async with SessionLocal() as session:
                status = await session.scalar(
                    select(Run.status).where(Run.id == run_id)
                )
            if status in _TERMINAL_RUN_STATES:
                return str(status)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            # Wake early when the broker signals completion; otherwise poll.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(broker_task), timeout=min(0.25, remaining)
                )
    finally:
        if not broker_task.done():
            broker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await broker_task


async def _last_node_output(session, run_id: str) -> object:
    """Return the output of the last node to finish in ``run_id``."""
    from app.models import NodeRun

    row = await session.scalar(
        select(NodeRun.output)
        .where(NodeRun.run_id == run_id)
        .order_by(NodeRun.finished_at.desc().nullslast())
        .limit(1)
    )
    if isinstance(row, dict):
        # Single-output nodes expose ``main``; fall back to the whole dict.
        return row.get("main", row)
    return row


async def wait_for_webhook_result(
    run_id: str, mode: str, response_code: int, timeout: float
) -> dict:
    """Wait for a synchronous webhook run and return a response shape.

    Shape matches ``_webhook_on_received_response`` so the route renders it
    uniformly: ``{status, headers, body, no_body, content_type?}``. Timeout →
    504; run error/cancel → 500; success → the respond_to_webhook record
    (Respond Node) or the last node's output (Last Node, or Respond Node with
    nothing recorded).
    """
    status = await _await_run_terminal(run_id, timeout)
    if status is None:
        return {
            "status": 504,
            "headers": {},
            "body": {"error": "Workflow did not respond in time", "run_id": run_id},
            "no_body": False,
        }
    if status != "success":
        return {
            "status": 500,
            "headers": {},
            "body": {"error": "Workflow run failed", "run_id": run_id},
            "no_body": False,
        }
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        recorded = getattr(run, "webhook_response", None) if run else None
        if mode == "Respond Node" and isinstance(recorded, dict):
            return {
                "status": _int_or(
                    recorded.get("status_code") or recorded.get("status"), 200
                ),
                "headers": recorded.get("headers") or {},
                "body": recorded.get("body"),
                "no_body": False,
                "content_type": recorded.get("content_type"),
            }
        body = await _last_node_output(session, run_id)
    return {
        "status": _int_or(response_code, 200),
        "headers": {},
        "body": body,
        "no_body": False,
    }


@dataclass
class WebhookDispatch:
    """Outcome of :func:`dispatch_webhook`.

    ``reject_status`` is the HTTP status to return when nothing ran but the path
    matched (403 IP / 401 auth); ``None`` otherwise. ``deduped`` is True when a
    matching node idempotently dropped a repeat delivery — the caller acks 200
    with no runs rather than treating the empty result as a rejection.
    ``response`` is the shaped ``On Received`` response from the first dispatched
    node (see :func:`_webhook_on_received_response`), or ``None`` for the default
    JSON ack.
    """

    run_ids: list[str] = field(default_factory=list)
    any_match: bool = False
    reject_status: int | None = None
    deduped: bool = False
    response: dict | None = None
    sync: dict | None = None


def _path_segments(value: str) -> list[str]:
    """Split a URL path into segments, ignoring surrounding slashes."""
    trimmed = value.strip("/")
    return trimmed.split("/") if trimmed else []


def _match_webhook_path(template: str, path: str) -> dict[str, str] | None:
    """Match a request ``path`` against a webhook ``path`` template.

    Templates may contain ``{name}`` placeholders that each capture exactly one
    path segment, e.g. ``products/{id}`` matches ``products/42`` and returns
    ``{"id": "42"}``; ``customers/{cid}/orders/{oid}`` captures both. A template
    with no placeholders matches by exact, segment-wise equality, so existing
    flat webhooks behave exactly as before.

    Returns the captured params on a match (``{}`` when the template has none),
    or ``None`` when the path does not match. Surrounding slashes on either side
    are ignored.
    """
    t_segs = _path_segments(template)
    p_segs = _path_segments(path)
    if len(t_segs) != len(p_segs):
        return None
    params: dict[str, str] = {}
    for t_seg, p_seg in zip(t_segs, p_segs, strict=True):
        if len(t_seg) >= 2 and t_seg[0] == "{" and t_seg[-1] == "}":
            name = t_seg[1:-1]
            if not name:
                return None
            params[name] = p_seg
        elif t_seg != p_seg:
            return None
    return params


def _route_template(base_path: str, route_path: str) -> str:
    """Join an API Endpoint node's base path with one route's sub-path."""
    return f"{base_path.strip('/')}/{route_path.strip('/')}"


def _match_api_route(
    base_path: str,
    routes: list[dict],
    method: str,
    path: str,
) -> tuple[str, dict[str, str]] | None:
    """Pick the best-matching route for an API Endpoint node.

    ``routes`` is a list of ``{"method", "path", "output"}`` rows. A node acts
    as a router, so exactly one branch fires: the most *specific* matching route
    wins, where specificity is the number of literal (non-``{param}``) segments
    in its full template. Ties are broken by row order (earlier wins). Method
    must match the row's method (case-insensitive); a row with a blank method
    matches any method.

    Returns ``(output, params)`` for the winning route, or ``None`` if nothing
    matches.
    """
    best: tuple[int, str, dict[str, str]] | None = None
    for route in routes:
        route_method = str(route.get("method") or "").upper()
        if route_method and route_method != method.upper():
            continue
        template = _route_template(base_path, str(route.get("path") or ""))
        params = _match_webhook_path(template, path)
        if params is None:
            continue
        specificity = sum(
            1 for seg in _path_segments(template) if not seg.startswith("{")
        )
        if best is None or specificity > best[0]:
            best = (specificity, str(route.get("output") or ""), params)
    if best is None:
        return None
    return best[1], best[2]


async def dispatch_webhook(
    path: str,
    request_payload: dict,
    *,
    prefer_draft: bool = False,
    raw_body: bytes | None = None,
    client_ip: str | None = None,
) -> WebhookDispatch:
    """Run every active workflow that starts with a matching webhook node.

    When ``prefer_draft`` is True the dispatcher uses each workflow's
    in-editor draft graph instead of the latest published version. This is
    what the editor's test URL (``/webhook-test/{path}``) should use so the
    user can iterate on auth + flow without publishing first. Production
    URL (``/webhook/{path}``) always uses the published snapshot.

    Returns a :class:`WebhookDispatch`. The caller uses ``any_match`` to
    distinguish "no workflow at this path" (404) from "matched but rejected
    every candidate", ``reject_status`` for the 403/401 to return, and
    ``deduped`` to ack 200 on an idempotent repeat.
    """
    run_ids: list[str] = []
    any_match = False
    ip_rejected = False
    auth_rejected = False
    deduped = False
    shaped_response: dict | None = None
    sync_info: dict | None = None
    headers = request_payload.get("headers") or {}
    query = request_payload.get("query") or {}
    workflows = (
        await _all_workflows() if prefer_draft else await _active_workflows()
    )
    async with SessionLocal() as dispatch_session:
        for workflow in workflows:
            graph: dict | None = None
            version_number: int = 1
            version_id: str | None = None
            if prefer_draft and getattr(workflow, "draft_graph", None):
                graph = workflow.draft_graph
                # Anchor the run to the latest known version for history sanity,
                # but we never bump the version — drafts are not snapshots.
                if workflow.versions:
                    version_number = workflow.versions[-1].version
                    version_id = workflow.versions[-1].id
            else:
                if not workflow.versions:
                    continue
                latest = workflow.versions[-1]
                graph = latest.graph or {}
                version_number = latest.version
                version_id = latest.id
            if not graph:
                continue
            for node in graph.get("nodes", []):
                node_type = node.get("type")
                node_params = node.get("params") or {}
                # Resolve which trigger output to seed and what path params it
                # captured. `webhook_trigger` is a single endpoint; `api_endpoint`
                # routes the request to one of its named branches.
                if node_type == "webhook_trigger":
                    node_path = str(node_params.get("path", ""))
                    matched_params = _match_webhook_path(node_path, path)
                    if matched_params is None:
                        continue
                    # Method-aware routing: when the node pins an HTTP method, only
                    # the matching request method fires it, so e.g. `GET /items/{id}`
                    # and `DELETE /items/{id}` can live in separate workflows. A node
                    # with no method set matches any method (back-compat).
                    node_method = str(node_params.get("http_method") or "").upper()
                    req_method = str(request_payload.get("method") or "").upper()
                    if node_method and req_method and node_method != req_method:
                        continue
                    seed_output = "main"
                elif node_type == "api_endpoint":
                    route_match = _match_api_route(
                        str(node_params.get("base_path") or ""),
                        node_params.get("routes") or [],
                        str(request_payload.get("method") or ""),
                        path,
                    )
                    if route_match is None:
                        continue
                    seed_output, matched_params = route_match
                else:
                    continue
                any_match = True
                # Perimeter first: IP allowlist gates before any auth work so an
                # unlisted caller never reaches credential comparison. Distinct 403.
                if not _webhook_ip_allowed(node_params, client_ip, headers):
                    ip_rejected = True
                    continue
                resolved_auth = await _resolve_node_auth(
                    node_params, workflow.id, workflow.environment_id,
                    session=dispatch_session,
                )
                if not _webhook_auth_passes(node_params, resolved_auth, headers, query):
                    auth_rejected = True
                    continue
                if not _webhook_hmac_passes(node_params, resolved_auth, headers, raw_body):
                    auth_rejected = True
                    continue
                # Idempotency: a repeat key for this workflow is acknowledged
                # without starting a second run. Production only — the editor test
                # URL should re-fire freely while iterating.
                dedup_key = _webhook_dedup_key(node_params, request_payload)
                if dedup_key is not None and not prefer_draft:
                    seen = await dispatch_session.scalar(
                        select(Run.id)
                        .where(Run.workflow_id == workflow.id)
                        .where(Run.deduplication_key == dedup_key)
                        .limit(1)
                    )
                    if seen is not None:
                        deduped = True
                        continue
                # Raw-body capture: when on, write the exact bytes as a run-scoped
                # artifact and expose the ref on the payload so binary/multipart
                # uploads reach the workflow without bloating the DB. Requires a
                # pre-generated run id so the artifact lives under runs/<run_id>/.
                # Expose the captured path params (e.g. `{{ $json.params.id }}`).
                # Built per node since each template captures different segments.
                node_payload = {**request_payload, "params": matched_params}
                raw_ref: dict | None = None
                pre_run_id: str | None = None
                if (
                    str(node_params.get("raw_body") or "off").lower() == "on"
                    and raw_body
                ):
                    from uuid import uuid4

                    pre_run_id = uuid4().hex
                    raw_ref = _capture_raw_body_artifact(
                        raw_body, headers, pre_run_id, node["id"]
                    )
                    if raw_ref is not None:
                        node_payload = {**node_payload, "raw_body": raw_ref}
                    else:
                        pre_run_id = None
                run_id = await start_run(
                    workflow.id,
                    graph,
                    version_number,
                    workflow_version_id=version_id,
                    mode="test" if prefer_draft else "production",
                    trigger_type="webhook",
                    cache={node["id"]: {seed_output: node_payload}},
                    trigger_node_id=node["id"],
                    deduplication_key=dedup_key,
                    run_id=pre_run_id,
                )
                run_ids.append(run_id)
                if raw_ref is not None:
                    # Create the Artifact row (and rehome to the configured backend)
                    # even if no downstream node carries the ref, so it's tracked by
                    # retention and downloadable. Idempotent with the engine's own
                    # persistence of refs that flow through node outputs.
                    from app.services.artifacts import persist_artifact_refs

                    await persist_artifact_refs(run_id, [raw_ref])
                # The first dispatched node owns the response. On Received shapes an
                # immediate ack; Last Node / Respond Node ask the caller to wait for
                # the run to finish (handled in the production webhook route).
                if shaped_response is None and sync_info is None:
                    mode = str(node_params.get("response_mode") or "On Received")
                    if mode in ("Last Node", "Respond Node") and not prefer_draft:
                        sync_info = {
                            "run_id": run_id,
                            "mode": mode,
                            "response_code": _int_or(node_params.get("response_code"), 200),
                        }
                    else:
                        shaped_response = _webhook_on_received_response(
                            node_params, request_payload
                        )
    reject_status: int | None = None
    if not run_ids and any_match and not deduped:
        # 403 only when every failure was IP-based (no auth was attempted).
        # If any workflow made it past the IP check but failed auth, return 401.
        reject_status = 403 if (ip_rejected and not auth_rejected) else 401
    return WebhookDispatch(
        run_ids=run_ids,
        any_match=any_match,
        reject_status=reject_status,
        deduped=deduped,
        response=shaped_response,
        sync=sync_info,
    )


async def _all_workflows() -> list[Workflow]:
    """Used by the editor test URL — draft-mode dispatch ignores `active`."""
    async with SessionLocal() as session:
        result = await session.scalars(
            select(Workflow).options(selectinload(Workflow.versions))
        )
        return list(result.all())


def _deployment_params(deployment: Deployment) -> dict:
    """Build the cron/interval-shaped params dict that ``_is_due`` expects."""
    return {
        "cron": deployment.schedule_cron or "",
        "interval": deployment.schedule_interval or "hours",
        "every": deployment.schedule_every or 1,
        "tz": deployment.schedule_tz or "",
    }


async def _load_active_poll_subscriptions() -> list[ProviderTriggerSubscription]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(ProviderTriggerSubscription).where(
                ProviderTriggerSubscription.status == "active"
            )
        )
        return list(rows.all())


async def _execute_poll(
    sub: ProviderTriggerSubscription,
    now: datetime,
) -> None:
    from noodle_nodes.integrations_v2.registry import get_registered_provider_trigger
    from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext

    registered = get_registered_provider_trigger(sub.node_type)
    spec = registered.spec

    async with SessionLocal() as session:
        workflow = await session.get(
            Workflow, sub.workflow_id, options=[selectinload(Workflow.versions)]
        )
        if workflow is None or not workflow.active or not workflow.versions:
            return
        version = workflow.versions[-1]
        graph = version.graph or {}
        node_params: dict = {}
        for node in graph.get("nodes", []):
            if node.get("id") == sub.node_id:
                node_params = node.get("params") or {}
                break
        from app.services.credentials import resolve_credential_refs
        resolved = await resolve_credential_refs(
            session,
            node_params,
            workflow_id=sub.workflow_id,
            environment_id=getattr(workflow, "environment_id", None),
        )

    cursor = (sub.config or {}).get("poll_cursor") or {}
    ctx = ProviderTriggerPollContext(params=resolved, cursor=cursor)
    result = await asyncio.to_thread(spec.poll, ctx)

    next_poll_at = (now + timedelta(seconds=spec.poll_interval_seconds)).isoformat()

    async with SessionLocal() as session:
        row = await session.get(ProviderTriggerSubscription, sub.id)
        if row is None:
            return
        row.config = {
            **(row.config or {}),
            "poll_cursor": result.cursor,
            "next_poll_at": next_poll_at,
        }
        if result.events:
            row.last_event_at = now
        await session.commit()

    if not result.events:
        return

    async with SessionLocal() as session:
        workflow = await session.get(
            Workflow, sub.workflow_id, options=[selectinload(Workflow.versions)]
        )
        if workflow is None or not workflow.versions:
            return
        version = workflow.versions[-1]
        graph = version.graph or {}

    for event_payload in result.events:
        await start_run(
            sub.workflow_id,
            graph,
            version.version,
            workflow_version_id=version.id,
            mode="production",
            trigger_type="provider_trigger",
            trigger_node_id=sub.node_id,
            cache={sub.node_id: {"main": event_payload}},
        )


async def _poll_subscriptions(now: datetime) -> None:
    """Fire poll hooks for any active provider trigger subscriptions that are due."""
    from noodle_nodes.integrations_v2.registry import (
        get_registered_provider_trigger,
        is_registered_provider_trigger,
    )

    subs = await _load_active_poll_subscriptions()
    for sub in subs:
        if not is_registered_provider_trigger(sub.node_type):
            continue
        registered = get_registered_provider_trigger(sub.node_type)
        if registered.spec.poll is None:
            continue

        config = sub.config or {}
        next_poll_str = config.get("next_poll_at")
        if next_poll_str:
            try:
                next_poll = datetime.fromisoformat(next_poll_str)
                if next_poll.tzinfo is None:
                    next_poll = next_poll.replace(tzinfo=UTC)
                if now < next_poll:
                    continue
            except (ValueError, TypeError):
                pass

        try:
            await _execute_poll(sub, now)
        except Exception:
            logger.warning(
                "poll failed for subscription %s (%s)", sub.id, sub.node_type, exc_info=True
            )
            async with SessionLocal() as session:
                row = await session.get(ProviderTriggerSubscription, sub.id)
                if row is not None:
                    row.error = "Poll hook raised an exception — check logs"
                    await session.commit()


async def _tick() -> None:
    """One pass of the scheduler.

    Precedence: a workflow with *any* active Deployment is scheduled **only**
    by its deployments (deployment is the source of truth). Workflows with
    no active deployment fall back to their in-graph ``schedule_trigger``,
    which preserves the zero-config default for legacy graphs.
    """
    now = datetime.now(UTC)
    due_workflow: list[tuple[str, dict, int, str | None, str]] = []
    due_deployment: list[tuple[str, dict, int, str | None, str, dict, str | None]] = []

    async with SessionLocal() as session:
        workflows = (
            await session.scalars(
                select(Workflow)
                .where(Workflow.active.is_(True))
                .options(selectinload(Workflow.versions))
            )
        ).all()
        deployments = (
            await session.scalars(select(Deployment).where(Deployment.active.is_(True)))
        ).all()
        states = {
            s.workflow_id: s
            for s in (await session.scalars(select(ScheduleState))).all()
        }

        wf_by_id = {wf.id: wf for wf in workflows}
        deployments_by_workflow: dict[str, list[Deployment]] = {}
        for d in deployments:
            deployments_by_workflow.setdefault(d.workflow_id, []).append(d)

        # Batch-load the pinned versions referenced by active deployments so the
        # loop below doesn't issue one ``session.get(WorkflowVersion)`` per
        # deployment every tick.
        dep_version_ids = {
            d.workflow_version_id for d in deployments if d.workflow_version_id
        }
        versions_by_id: dict[str, WorkflowVersion] = {}
        if dep_version_ids:
            versions_by_id = {
                v.id: v
                for v in (
                    await session.scalars(
                        select(WorkflowVersion).where(
                            WorkflowVersion.id.in_(dep_version_ids)
                        )
                    )
                ).all()
            }

        # --- 1. Active deployments take precedence over in-graph schedules.
        for deployment in deployments:
            workflow = wf_by_id.get(deployment.workflow_id)
            if workflow is None:
                continue
            version: WorkflowVersion | None = None
            if deployment.workflow_version_id:
                version = versions_by_id.get(deployment.workflow_version_id)
            if version is None:
                version = workflow.versions[-1]
            graph = version.graph or {}
            params = _deployment_params(deployment)
            if deployment.last_fired is None:
                deployment.last_fired = now  # start the clock, no fire
                continue
            if _is_due(params, deployment.last_fired, now):
                deployment.last_fired = now
                chosen_trigger = first_trigger_node(graph)
                trigger_id = (
                    chosen_trigger["id"]
                    if isinstance(chosen_trigger, dict)
                    else getattr(chosen_trigger, "id", None)
                )
                due_deployment.append(
                    (
                        workflow.id,
                        graph,
                        version.version,
                        version.id,
                        deployment.id,
                        deployment.default_parameters or {},
                        trigger_id,
                    )
                )

        # --- 2. Fallback: workflows that are active and have no deployment
        # use their in-graph schedule_trigger as before.
        for workflow in workflows:
            if not workflow.active:
                continue
            if deployments_by_workflow.get(workflow.id):
                continue  # deployment(s) own this workflow's schedule
            if not workflow.versions:
                continue  # no version yet — skip rather than IndexError
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            schedule = next(
                (
                    n
                    for n in graph.get("nodes", [])
                    if n.get("type") == "schedule_trigger"
                ),
                None,
            )
            if schedule is None:
                continue

            params = schedule.get("params", {})
            state = states.get(workflow.id)
            if state is None:
                session.add(ScheduleState(workflow_id=workflow.id, last_fired=now))
                continue
            if _is_due(params, state.last_fired, now):
                state.last_fired = now
                due_workflow.append(
                    (
                        workflow.id,
                        graph,
                        latest.version,
                        latest.id,
                        schedule["id"],
                    )
                )

        await session.commit()

    if due_workflow or due_deployment:
        logger.info(
            "scheduler tick: %d workflow schedule(s), %d deployment(s) due",
            len(due_workflow),
            len(due_deployment),
        )

    # Dispatch outside the state transaction; start_run opens its own session.
    for workflow_id, graph, version, version_id, trigger_id in due_workflow:
        await start_run(
            workflow_id,
            graph,
            version,
            workflow_version_id=version_id,
            mode="production",
            trigger_type="schedule",
            trigger_node_id=trigger_id,
        )
    for (
        workflow_id,
        graph,
        version,
        version_id,
        deployment_id,
        params,
        trigger_id,
    ) in due_deployment:
        await start_run(
            workflow_id,
            graph,
            version,
            workflow_version_id=version_id,
            deployment_id=deployment_id,
            mode="production",
            trigger_type="deployment",
            parameters=params or None,
            trigger_node_id=trigger_id,
        )

    # --- 3. Fire poll hooks for due provider trigger subscriptions.
    await _poll_subscriptions(now)


async def scheduler_loop() -> None:
    """Background loop that fires schedule triggers. Started from the lifespan."""
    _backoff: float = 0.0
    while True:
        try:
            await _tick()
            _backoff = 0.0
        except Exception:
            logger.exception("scheduler tick failed")
            _backoff = min(_backoff * 2 if _backoff else 5.0, 300.0)
        await asyncio.sleep(30 + _backoff)
