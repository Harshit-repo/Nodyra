"""JSON responses that can never crash on non-finite floats.

Starlette's ``JSONResponse`` serializes with ``allow_nan=False``, so a single
NaN/Infinity anywhere in a payload (e.g. a legacy ``NodeRun.output`` written
before non-finite sanitization was added to ``nodyra.serialization``) raises
``ValueError: Out of range float values are not JSON compliant: nan`` and turns
the whole response into a 500. ``NodyraJSONResponse`` sanitizes the payload
first, degrading NaN/Inf to ``null`` instead of failing the request.

New outputs are sanitized at ingestion
(``nodyra.serialization.serialize_value``), so this class is the safety net for
already-persisted data and any path that bypasses serialization.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from nodyra.serialization import sanitize_nonfinite


class NodyraJSONResponse(JSONResponse):
    """``JSONResponse`` that replaces non-finite floats with null before encoding."""

    def render(self, content: Any) -> bytes:
        return super().render(sanitize_nonfinite(content))
