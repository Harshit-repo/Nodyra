import httpx

from app.worker_main import _serve_metrics


async def test_metrics_server_serves_openmetrics():
    server = await _serve_metrics(0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/metrics")
        assert resp.status_code == 200
        assert "noodle" in resp.text or "#" in resp.text
    finally:
        server.close()
        await server.wait_closed()
