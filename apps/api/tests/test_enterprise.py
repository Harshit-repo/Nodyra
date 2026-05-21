from httpx import AsyncClient


async def test_register_login_and_me(client: AsyncClient) -> None:
    registered = (
        await client.post(
            "/auth/register",
            json={"email": "dev@noodle.test", "password": "supersecret"},
        )
    ).json()
    assert registered["user"]["email"] == "dev@noodle.test"
    token = registered["token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "dev@noodle.test"

    login = await client.post(
        "/auth/login",
        json={"email": "dev@noodle.test", "password": "supersecret"},
    )
    assert login.status_code == 200


async def test_login_rejects_bad_password(client: AsyncClient) -> None:
    await client.post(
        "/auth/register",
        json={"email": "a@b.test", "password": "password123"},
    )
    resp = await client.post(
        "/auth/login", json={"email": "a@b.test", "password": "wrong-password"}
    )
    assert resp.status_code == 401


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_credentials_never_expose_secret_values(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/credentials",
            json={
                "name": "GitHub Token",
                "type": "apiKey",
                "data": {"token": "ghp_supersecret"},
            },
        )
    ).json()
    assert created["keys"] == ["token"]
    assert "ghp_supersecret" not in str(created)

    listed = (await client.get("/credentials")).json()
    assert "ghp_supersecret" not in str(listed)


async def test_credential_delete(client: AsyncClient) -> None:
    cred_id = (
        await client.post(
            "/credentials", json={"name": "Temp", "data": {"k": "v"}}
        )
    ).json()["id"]
    assert (await client.delete(f"/credentials/{cred_id}")).status_code == 204


async def test_auth_required_endpoint(client: AsyncClient) -> None:
    initial = (await client.get("/auth/required")).json()
    assert initial["auth_required"] is False
    assert initial["signed_in"] is False

    registered = (
        await client.post(
            "/auth/register",
            json={"email": "user@noodle.test", "password": "supersecret"},
        )
    ).json()
    info = (
        await client.get(
            "/auth/required",
            headers={"Authorization": f"Bearer {registered['token']}"},
        )
    ).json()
    assert info["signed_in"] is True
    assert info["user"]["email"] == "user@noodle.test"


async def test_auth_gating_blocks_when_required(client: AsyncClient) -> None:
    from app.config import settings as app_settings

    app_settings.auth_required = True
    try:
        # /auth and /health are still reachable so the user can sign in.
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/auth/required")).status_code == 200

        # Other endpoints reject anonymous requests.
        assert (await client.get("/workflows")).status_code == 401

        registered = (
            await client.post(
                "/auth/register",
                json={"email": "gate@noodle.test", "password": "supersecret"},
            )
        ).json()
        headers = {"Authorization": f"Bearer {registered['token']}"}
        assert (
            await client.get("/workflows", headers=headers)
        ).status_code == 200

        # Bad token still fails.
        bad = await client.get(
            "/workflows", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert bad.status_code == 401
    finally:
        app_settings.auth_required = False


async def test_audit_log_records_actions(client: AsyncClient) -> None:
    await client.post("/workflows", json={"name": "Audited Flow"})
    await client.post("/credentials", json={"name": "Audited Cred", "data": {}})

    events = (await client.get("/audit")).json()
    actions = {(e["action"], e["target_type"]) for e in events}
    assert ("create", "workflow") in actions
    assert ("create", "credential") in actions
