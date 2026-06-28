import html as html_lib
import json
import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Credential, Environment, User, Workflow
from app.schemas import (
    CredentialCreate,
    CredentialInfo,
    CredentialOAuthStartRequest,
    CredentialOAuthStartResponse,
    CredentialTestDraftRequest,
    CredentialTestRequest,
    CredentialTestResponse,
    CredentialTypeInfo,
    CredentialUpdate,
    PageResponse,
)
from app.security import get_client_ip, optional_current_user, require_permission
from app.services import org_keys
from app.services.audit import log_audit
from app.services.credential_tests import (
    available_test_services,
    test_credential_connection,
)
from app.services.credential_types import get_credential_type, list_credential_types
from app.services.crypto import decrypt_credential
from app.services.oauth import (
    OAuthError,
    build_authorization_url,
    create_oauth_state,
    decode_oauth_state,
    default_scopes,
    exchange_authorization_code,
    parse_expires_at,
    refresh_stored_credential,
    resolve_redirect_uri,
    scopes_from_credential_data,
    token_payload_to_credential_data,
)
from app.services.redaction import invalidate_secret_cache
from app.tenancy import active_org_id, current_org_id

router = APIRouter(prefix="/credentials", tags=["credentials"])
logger = logging.getLogger(__name__)

SCOPES = {"global", "environment", "workflow", "runner_pool"}


def _info(cred: Credential, org_kek: bytes | None = None) -> CredentialInfo:
    data = decrypt_credential(cred.encrypted_data, cred.encrypted_dek, org_kek=org_kek)
    if not data and cred.encrypted_data:
        logger.warning(
            "credential %s (%s) decrypted to empty dict — possible key mismatch "
            "or corrupt ciphertext (B-09)",
            cred.id, cred.name,
        )
    type_spec = get_credential_type(cred.type)
    return CredentialInfo(
        id=cred.id,
        name=cred.name,
        type=cred.type,
        auth_method=type_spec.auth_method if type_spec else None,
        scope=cred.scope,
        workflow_id=cred.workflow_id,
        environment_id=cred.environment_id,
        runner_pool_id=cred.runner_pool_id,
        description=cred.description,
        keys=sorted(data.keys()),
        oauth_scopes=scopes_from_credential_data(data)
        if type_spec and type_spec.auth_method == "oauth2"
        else [],
        oauth_expires_at=parse_expires_at(data.get("expires_at"))
        if type_spec and type_spec.auth_method == "oauth2"
        else None,
        last_used_at=cred.last_used_at,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


def _oauth_http_error(exc: OAuthError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _load(session: AsyncSession, cred_id: str) -> Credential:
    # populate_existing=True forces a real SELECT instead of returning an
    # identity-map hit. The map can hold a Credential loaded earlier in the
    # same session under skip_org_filter=True (e.g. a dispatch path); without
    # the SELECT the org-filter hook never fires and a caller in org-A could
    # read/update/delete org-B's credential (cross-tenant IDOR / R-1).
    cred = await session.get(Credential, cred_id, populate_existing=True)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return cred


async def _validate_scope(
    session: AsyncSession,
    scope: str,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> None:
    if scope not in SCOPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported credential scope '{scope}'.",
        )
    if scope == "workflow":
        if not workflow_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "workflow_id is required for workflow-scoped credentials.",
            )
        if await session.get(Workflow, workflow_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    if scope == "environment":
        if not environment_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "environment_id is required for environment-scoped credentials.",
            )
        if await session.get(Environment, environment_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found")
    if scope == "runner_pool" and not runner_pool_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "runner_pool_id is required for runner-pool-scoped credentials.",
        )


def _scope_rank(
    cred: Credential,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> int:
    if cred.scope == "workflow" and cred.workflow_id == workflow_id:
        return 40
    if cred.scope == "environment" and cred.environment_id == environment_id:
        return 30
    if cred.scope == "runner_pool" and cred.runner_pool_id == runner_pool_id:
        return 20
    if cred.scope == "global":
        return 10
    return -1


async def _resolve(
    session: AsyncSession,
    *,
    name: str,
    type: str | None,
    workflow_id: str | None,
    environment_id: str | None,
    runner_pool_id: str | None,
) -> Credential | None:
    stmt = select(Credential).where(Credential.name == name)
    if type:
        stmt = stmt.where(Credential.type == type)
    rows = (await session.scalars(stmt)).all()
    candidates = [
        (rank, cred)
        for cred in rows
        if (rank := _scope_rank(cred, workflow_id, environment_id, runner_pool_id)) >= 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


@router.get(
    "/types",
    response_model=list[CredentialTypeInfo],
    dependencies=[Depends(require_permission("credential:read"))],
)
async def list_types() -> list[CredentialTypeInfo]:
    """Credential definitions owned by the backend.

    The editor should use these specs to render credential creation forms and
    OAuth connect actions instead of hard-coded frontend presets.
    """
    return [
        CredentialTypeInfo.model_validate(spec, from_attributes=True)
        for spec in list_credential_types()
    ]


@router.post(
    "/oauth/start",
    response_model=CredentialOAuthStartResponse,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def start_oauth_credential(
    body: CredentialOAuthStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
) -> CredentialOAuthStartResponse:
    await _validate_scope(
        session,
        body.scope,
        body.workflow_id,
        body.environment_id,
        body.runner_pool_id,
    )
    try:
        scopes = default_scopes(body.credential_type, body.scopes)
        redirect_uri = resolve_redirect_uri(
            body.redirect_uri,
            str(request.url_for("oauth_callback")),
        )
        state, expires_at = create_oauth_state(
            {
                "credential_type": body.credential_type,
                "name": body.name,
                "scope": body.scope,
                "workflow_id": body.workflow_id,
                "environment_id": body.environment_id,
                "runner_pool_id": body.runner_pool_id,
                "description": body.description,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                # Bind the initiating org into the signed state. The callback is a
                # provider redirect with no X-Org-Id header, so without this the
                # credential would be stamped/encrypted under DEFAULT_ORG_ID for
                # every non-default org (cross-tenant leak / wrong KEK — R-9).
                "org_id": active_org_id(),
                "actor_id": actor.id if actor else None,
                "actor_email": actor.email if actor else None,
            }
        )
        authorization_url = build_authorization_url(
            type_id=body.credential_type,
            redirect_uri=redirect_uri,
            state=state,
            scopes=scopes,
        )
    except OAuthError as exc:
        raise _oauth_http_error(exc) from exc
    return CredentialOAuthStartResponse(
        authorization_url=authorization_url,
        state=state,
        credential_type=body.credential_type,
        redirect_uri=redirect_uri,
        scopes=scopes,
        expires_at=expires_at,
    )


@router.get("/oauth/callback", name="oauth_callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    request: Request = None,  # injected by FastAPI
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Handle provider OAuth redirect.

    Returns an HTML page that posts a ``noodle_oauth_success`` (or
    ``noodle_oauth_error``) message to the opener window and closes itself.  If
    no opener is present (direct navigation) the page shows a brief status
    message instead.

    Rate-limited per IP to mitigate brute-force state-guessing and the
    callback's exemption from the auth gate and CSRF middleware.
    """
    # Per-IP sliding-window cap — same primitive as /auth/login.
    from app.services import rate_limit as _rl

    ip = get_client_ip(request)
    allowed = await _rl.allow(
        "oauth_callback", ip, limit=20, window_seconds=60,
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many OAuth callback requests; try again in a minute.",
        )

    if error:
        return _oauth_popup_html(
            success=False,
            message=error_description or error,
            credential_id=None,
        )
    if not code or not state:
        return _oauth_popup_html(
            success=False,
            message="OAuth callback requires code and state.",
            credential_id=None,
        )
    try:
        payload = decode_oauth_state(state)
        credential_type = str(payload["credential_type"])
        scopes = [
            str(scope)
            for scope in payload.get("scopes", [])
            if str(scope).strip()
        ]
        token_payload = await exchange_authorization_code(
            type_id=credential_type,
            code=code,
            redirect_uri=str(payload["redirect_uri"]),
            scopes=scopes,
        )
        data = token_payload_to_credential_data(
            type_id=credential_type,
            token_payload=token_payload,
            requested_scopes=scopes,
        )
    except OAuthError as exc:
        return _oauth_popup_html(success=False, message=str(exc), credential_id=None)

    scope = str(payload["scope"])
    workflow_id = payload.get("workflow_id")
    environment_id = payload.get("environment_id")
    runner_pool_id = payload.get("runner_pool_id")

    # Re-establish the org the flow was started under (carried in the signed
    # state) so scope validation sees the right org's resources, the credential
    # is stamped to that org, and it is encrypted under that org's KEK (R-9).
    # ``org_id`` is absent for states minted before this fix and for the
    # single-tenant default — fall back to the request's current context.
    state_org_id = payload.get("org_id")
    org_token = (
        current_org_id.set(str(state_org_id)) if state_org_id is not None else None
    )
    try:
        await _validate_scope(
            session,
            scope,
            str(workflow_id) if workflow_id else None,
            str(environment_id) if environment_id else None,
            str(runner_pool_id) if runner_pool_id else None,
        )
        _enc_data, _enc_dek = await org_keys.encrypt_credential_current(data, session)
        cred = Credential(
            name=str(payload["name"]),
            type=credential_type,
            scope=scope,
            workflow_id=str(workflow_id) if scope == "workflow" and workflow_id else None,
            environment_id=(
                str(environment_id) if scope == "environment" and environment_id else None
            ),
            runner_pool_id=(
                str(runner_pool_id) if scope == "runner_pool" and runner_pool_id else None
            ),
            description=str(payload.get("description") or ""),
            encrypted_data=_enc_data,
            encrypted_dek=_enc_dek,
        )
        session.add(cred)
        await log_audit(
            session,
            "create",
            "credential",
            detail=cred.name,
            actor_id=str(payload.get("actor_id") or "") or None,
            actor_email=str(payload.get("actor_email") or "") or None,
        )
        await session.commit()
        await session.refresh(cred)
    finally:
        if org_token is not None:
            current_org_id.reset(org_token)
    invalidate_secret_cache(cred.org_id)
    return _oauth_popup_html(
        success=True,
        message=f"Connected \u2014 {cred.name}",
        credential_id=str(cred.id),
    )


def _oauth_popup_html(
    *,
    success: bool,
    message: str,
    credential_id: str | None,
) -> HTMLResponse:
    """Return an HTML page that communicates back to the opener and closes.

    The parent window listens for ``noodle_oauth_success`` / ``noodle_oauth_error``
    messages and refreshes the credentials list accordingly.
    """
    event_type = "noodle_oauth_success" if success else "noodle_oauth_error"
    safe_html_message = html_lib.escape(message, quote=True)
    # JSON encoding handles quotes/backslashes/control characters. Escaping the
    # HTML closing delimiter prevents a provider-controlled error string from
    # terminating the script element early.
    message_js = json.dumps(message).replace("</", "<\\/")
    event_js = json.dumps(event_type)
    cred_id_js = json.dumps(credential_id)
    nonce = secrets.token_urlsafe(24)
    bg = "#1a2633" if success else "#2d1a1a"
    icon = "\u2705" if success else "\u274c"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Noodle &#8212; OAuth</title>
  <style nonce="{nonce}">
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:{bg};color:#e0e6ed;font-family:system-ui,sans-serif;
         display:flex;align-items:center;justify-content:center;min-height:100vh}}
    .card{{background:#1f2d3d;border-radius:12px;padding:32px 40px;
           text-align:center;max-width:360px;box-shadow:0 8px 32px #0006}}
    h2{{font-size:2rem;margin-bottom:8px}}
    p{{color:#8aa;font-size:.95rem;margin-top:8px}}
    small{{display:block;color:#556;margin-top:16px;font-size:.8rem}}
  </style>
</head>
<body>
  <div class="card">
    <h2>{icon}</h2>
    <p id="msg">{safe_html_message}</p>
    <small>This window will close automatically.</small>
  </div>
  <script nonce="{nonce}">
    (function () {{
      var payload = {{
        type: {event_js},
        credentialId: {cred_id_js},
        message: {message_js}
      }};
      if (window.opener && !window.opener.closed) {{
        try {{
          window.opener.postMessage(payload, window.location.origin);
        }} catch (e) {{}}
      }}
      // Give the parent a moment to receive the message before closing
      setTimeout(function () {{ window.close(); }}, 800);
    }})();
  </script>
</body>
</html>
"""
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return HTMLResponse(
        content=html,
        status_code=200,
        headers={"Content-Security-Policy": csp},
    )


@router.post(
    "/{cred_id}/refresh",
    response_model=CredentialInfo,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def refresh_credential(
    cred_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
) -> CredentialInfo:
    cred = await _load(session, cred_id)
    data = await org_keys.decrypt_credential_for(cred, session)
    try:
        await refresh_stored_credential(cred, data, session)
    except OAuthError as exc:
        raise _oauth_http_error(exc) from exc
    await log_audit(
        session,
        "update",
        "credential",
        cred.id,
        f"{cred.name} refreshed",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    await session.refresh(cred)
    invalidate_secret_cache(cred.org_id)
    return _info(cred, await org_keys.get_org_kek(cred.org_id, session))


@router.get(
    "/test-handlers",
    response_model=list[str],
    dependencies=[Depends(require_permission("credential:read"))],
)
async def list_credential_test_handlers() -> list[str]:
    """Service ids whose credentials can be validated via ``POST /credentials/{id}/test``.

    The editor reads this to decide whether to surface a "Test connection" action
    next to a credential whose ``CredentialSpec.test_service`` (or ``type`` fallback)
    appears in the list. Stable alphabetical order.
    """
    return available_test_services()


@router.get(
    "",
    response_model=PageResponse[CredentialInfo],
    dependencies=[Depends(require_permission("credential:read"))],
)
async def list_credentials(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(select(func.count()).select_from(Credential))
    result = await session.scalars(
        select(Credential)
        .order_by(Credential.name)
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()
    # B-11: batch-fetch all org KEKs in one query instead of one per unique org.
    unique_org_ids = list({c.org_id for c in rows})
    keks = await org_keys.batch_get_org_keks(unique_org_ids, session)
    items = [_info(c, keks.get(c.org_id)) for c in rows]
    return PageResponse(items=items, total=total or 0, limit=limit, offset=offset)


@router.get(
    "/resolve",
    response_model=CredentialInfo,
    dependencies=[Depends(require_permission("credential:read_values"))],
)
async def resolve_credential(
    name: str = Query(min_length=1),
    type: str | None = None,
    workflow_id: str | None = None,
    environment_id: str | None = None,
    runner_pool_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    cred = await _resolve(
        session,
        name=name,
        type=type,
        workflow_id=workflow_id,
        environment_id=environment_id,
        runner_pool_id=runner_pool_id,
    )
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    # Resolve is a lookup, not a use. ``resolve_credential_refs`` updates
    # ``last_used_at`` at workflow dispatch time, which is the real "used"
    # event. Keeping GET /credentials/resolve side-effect-free avoids
    # surprising last_used_at writes from caches / retries / prefetchers.
    return _info(cred, await org_keys.get_org_kek(cred.org_id, session))


@router.post(
    "",
    response_model=CredentialInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def create_credential(
    body: CredentialCreate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    await _validate_scope(
        session,
        body.scope,
        body.workflow_id,
        body.environment_id,
        body.runner_pool_id,
    )
    _enc_data, _enc_dek = await org_keys.encrypt_credential_current(body.data, session)
    cred = Credential(
        name=body.name,
        type=body.type,
        scope=body.scope,
        workflow_id=body.workflow_id if body.scope == "workflow" else None,
        environment_id=body.environment_id if body.scope == "environment" else None,
        runner_pool_id=body.runner_pool_id if body.scope == "runner_pool" else None,
        description=body.description,
        encrypted_data=_enc_data,
        encrypted_dek=_enc_dek,
    )
    session.add(cred)
    await log_audit(session, "create", "credential", detail=body.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(cred)
    invalidate_secret_cache(cred.org_id)
    return _info(cred, await org_keys.get_org_kek(cred.org_id, session))


@router.put(
    "/{cred_id}",
    response_model=CredentialInfo,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def update_credential(
    cred_id: str,
    body: CredentialUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    cred = await _load(session, cred_id)
    if body.name is not None:
        cred.name = body.name
    scope = body.scope or cred.scope
    workflow_id = body.workflow_id if body.workflow_id is not None else cred.workflow_id
    environment_id = (
        body.environment_id if body.environment_id is not None else cred.environment_id
    )
    runner_pool_id = (
        body.runner_pool_id if body.runner_pool_id is not None else cred.runner_pool_id
    )
    if body.scope is not None or any(
        value is not None
        for value in (body.workflow_id, body.environment_id, body.runner_pool_id)
    ):
        await _validate_scope(
            session,
            scope,
            workflow_id,
            environment_id,
            runner_pool_id,
        )
        cred.scope = scope
        cred.workflow_id = workflow_id if scope == "workflow" else None
        cred.environment_id = environment_id if scope == "environment" else None
        cred.runner_pool_id = runner_pool_id if scope == "runner_pool" else None
    if body.description is not None:
        cred.description = body.description
    if body.data is not None:
        cred.encrypted_data, cred.encrypted_dek = await org_keys.encrypt_credential_for(
            cred.org_id, body.data, session
        )
    await log_audit(session, "update", "credential", cred.id, cred.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(cred)
    if body.data is not None:
        invalidate_secret_cache(cred.org_id)
    return _info(cred, await org_keys.get_org_kek(cred.org_id, session))


@router.post(
    "/{cred_id}/test",
    response_model=CredentialTestResponse,
    dependencies=[Depends(require_permission("credential:test"))],
)
async def test_credential(
    cred_id: str,
    body: CredentialTestRequest,
    session: AsyncSession = Depends(get_session),
):
    cred = await _load(session, cred_id)
    if (
        _scope_rank(
            cred,
            body.workflow_id,
            body.environment_id,
            body.runner_pool_id,
        )
        < 0
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Credential is not visible for the supplied workflow/environment scope.",
        )
    data = await org_keys.decrypt_credential_for(cred, session)
    type_spec = get_credential_type(cred.type)
    test_service = type_spec.test_service if type_spec and type_spec.test_service else cred.type
    result = await test_credential_connection(test_service, data, body.context)
    cred.last_used_at = datetime.now(UTC)
    await session.commit()
    return result


@router.post(
    "/test-draft",
    response_model=CredentialTestResponse,
    dependencies=[Depends(require_permission("credential:test"))],
)
async def test_credential_draft(
    body: CredentialTestDraftRequest,
) -> CredentialTestResponse:
    """Test in-progress credential values before they are saved.

    Stateless: nothing is persisted. Used by the create-credential modals'
    "Test connection" button.
    """
    type_spec = get_credential_type(body.type)
    test_service = (
        type_spec.test_service if type_spec and type_spec.test_service else body.type
    )
    return await test_credential_connection(test_service, body.data, body.context)


@router.delete(
    "/{cred_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("credential:write"))],
)
async def delete_credential(
    cred_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    cred = await _load(session, cred_id)
    await log_audit(session, "delete", "credential", cred.id, cred.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.delete(cred)
    await session.commit()
    invalidate_secret_cache(cred.org_id)
