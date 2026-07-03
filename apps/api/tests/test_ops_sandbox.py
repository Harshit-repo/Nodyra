async def test_ops_sandbox_reports_mode(client):
    resp = await client.get("/ops/sandbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] in ("off", "auto", "required")
    assert body["active"] is False
    assert isinstance(body["idle"], int)
