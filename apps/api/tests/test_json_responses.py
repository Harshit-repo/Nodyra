"""NodyraJSONResponse must degrade non-finite floats instead of raising a 500.

Starlette's stock JSONResponse serializes with ``allow_nan=False``; a legacy
``NodeRun.output`` containing NaN (written before serialization-side
sanitization) used to raise ``ValueError: Out of range float values are not
JSON compliant: nan`` mid-encode and turn every MCP/REST read of that run into
a bare HTTP 500. This class is the read-boundary safety net.
"""

from __future__ import annotations

import json

from app.services.json_responses import NodyraJSONResponse


def test_render_sanitizes_non_finite_floats() -> None:
    response = NodyraJSONResponse(
        {
            "result": {
                "rows": [{"statistic": float("nan"), "p_value": float("inf")}, {"ok": 1.5}],
            }
        }
    )
    body = response.body
    payload = json.loads(body)
    assert payload["result"]["rows"][0] == {"statistic": None, "p_value": None}
    assert payload["result"]["rows"][1] == {"ok": 1.5}


def test_render_passes_through_plain_payloads() -> None:
    response = NodyraJSONResponse({"a": 1, "b": [True, None, "x"]})
    assert json.loads(response.body) == {"a": 1, "b": [True, None, "x"]}


def test_render_handles_status_codes_and_headers() -> None:
    response = NodyraJSONResponse(
        {"detail": "ok", "score": float("nan")},
        status_code=201,
        headers={"X-Test": "1"},
    )
    assert response.status_code == 201
    assert json.loads(response.body) == {"detail": "ok", "score": None}
