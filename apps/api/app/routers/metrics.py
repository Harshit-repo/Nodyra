"""Prometheus-compatible metrics endpoint."""

from fastapi import APIRouter, Request, Response

from app.services.metrics import get_metrics_text

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Expose process metrics in OpenMetrics text format.

    Access is gated by auth_gate (unless /metrics is added to the exempt
    list) so operators scraping Prometheus must supply a bearer token or
    configure the exemption in settings.
    """
    return Response(
        content=get_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
