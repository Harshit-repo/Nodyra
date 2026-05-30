from fastapi import APIRouter, HTTPException, Query, status

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from noodle.models import NodeManifest
from noodle.sdk import registry

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeManifest])
async def list_nodes(
    category: str | None = Query(default=None),
) -> list[NodeManifest]:
    """Return every registered node manifest — the source for the editor palette."""
    manifests = registry.manifests()
    if category:
        manifests = [m for m in manifests if m.category == category]
    return manifests


@router.get("/{node_type}", response_model=NodeManifest)
async def get_node(node_type: str) -> NodeManifest:
    for manifest in registry.manifests():
        if manifest.type == node_type:
            return manifest
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown node type '{node_type}'")

