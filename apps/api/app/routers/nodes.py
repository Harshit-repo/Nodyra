from fastapi import APIRouter

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from noodle.models import NodeManifest
from noodle.sdk import registry

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeManifest])
async def list_nodes() -> list[NodeManifest]:
    """Return every registered node manifest — the source for the editor palette."""
    return registry.manifests()
