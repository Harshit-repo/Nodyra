"""Live expression preview for the editor.

Evaluates a ``{{ }}`` value against caller-supplied ``$json`` / ``$input`` /
``$node`` context using the *same* evaluator the engine runs at execution time
(``noodle.expr``), so the preview is faithful — arithmetic, ``len()``, etc. all
behave exactly as they will in a real run. Stateless: the editor sends the
context it already has from the last run.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from noodle.expr import build_context, evaluate, evaluate_parts
from noodle.serialization import serialize_value

router = APIRouter(tags=["expressions"])


class ExpressionPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    value: str
    json_value: Any = Field(default=None, alias="json")  # $json
    inputs: dict[str, Any] = Field(default_factory=dict)  # $input
    nodes: dict[str, Any] = Field(default_factory=dict)  # $node


class ExpressionPreviewResponse(BaseModel):
    result: Any = None
    error: str | None = None
    parts: list[dict[str, Any]] = Field(default_factory=list)


def _unwrap(value: Any) -> Any:
    """Pull the raw data out of an ``expr._Attrible`` wrapper so it serializes."""
    inner = getattr(value, "_data", None)
    return inner if inner is not None else value


def _serialize_part(part: dict[str, Any]) -> dict[str, Any]:
    out = dict(part)
    if "value" in out:
        out["value"] = serialize_value(_unwrap(out["value"]))
    return out


@router.post("/expression-preview", response_model=ExpressionPreviewResponse)
async def preview_expression(
    body: ExpressionPreviewRequest,
) -> ExpressionPreviewResponse:
    context = build_context(
        first_input=body.json_value,
        inputs=body.inputs,
        node_outputs=body.nodes,
    )
    parts = [_serialize_part(p) for p in evaluate_parts(body.value, context)]
    try:
        result = evaluate(body.value, context)
    except Exception as exc:  # noqa: BLE001 - surface any eval failure
        return ExpressionPreviewResponse(
            error=f"{type(exc).__name__}: {exc}", parts=parts
        )
    # ``evaluate`` swallows errors into "[expr error: ...]" strings.
    if isinstance(result, str) and result.startswith("[expr error:"):
        return ExpressionPreviewResponse(error=result.strip("[]"), parts=parts)
    return ExpressionPreviewResponse(
        result=serialize_value(_unwrap(result)), parts=parts
    )
