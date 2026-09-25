"""Live expression preview for the editor.

Evaluates a ``{{ }}`` value against caller-supplied ``$json`` / ``$input`` /
``$node`` context using the *same* evaluator the engine runs at execution time
(``nodyra.expr``), so the preview is faithful — arithmetic, ``len()``, etc. all
behave exactly as they will in a real run. Stateless: the editor sends the
context it already has from the last run.

SECURITY (C1): evaluation happens in an isolated subprocess
(``services/expr_preview``) with a secret-free environment — never in this
process, which holds the master KEK and DB credentials. A sandbox escape in
``nodyra.expr`` must land in a process that knows nothing.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.security import require_permission
from app.services import expr_preview

router = APIRouter(tags=["expressions"])


class ExpressionPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    value: str = Field(max_length=20_000)
    json_value: Any = Field(default=None, alias="json")  # $json
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=1_000)  # $input
    nodes: dict[str, Any] = Field(default_factory=dict, max_length=1_000)  # $node


class ExpressionPreviewResponse(BaseModel):
    result: Any = None
    error: str | None = None
    parts: list[dict[str, Any]] = Field(default_factory=list)


@router.post(
    "/expression-preview",
    response_model=ExpressionPreviewResponse,
    # Evaluates a submitted expression in a subprocess — real compute on
    # attacker-chosen input. It is an authoring tool, so it belongs behind the
    # same permission as editing a workflow rather than being open to any
    # authenticated principal.
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def preview_expression(
    body: ExpressionPreviewRequest,
) -> ExpressionPreviewResponse:
    out = await expr_preview.preview(
        value=body.value,
        json_value=body.json_value,
        inputs=body.inputs,
        nodes=body.nodes,
    )
    return ExpressionPreviewResponse(
        result=out.get("result"), error=out.get("error"), parts=out.get("parts", [])
    )
