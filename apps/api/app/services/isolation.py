"""Write-time helpers for X4 execution isolation.

The dispatch-time gate in ``runner.start_run`` is the security boundary —
these checks exist so a dedicated_pool org gets a clear 422 when *assigning*
a non-qualifying pool, instead of a refused run later.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Organization, RunnerPool
from app.tenancy import run_as_system

QUALIFYING_PROVIDERS = ("docker", "kubernetes")


async def validate_pool_assignment(
    session: AsyncSession, org_id: str | None, pool_id: str | None
) -> None:
    """422 when a dedicated_pool org is handed a pool the dispatch gate would
    refuse (foreign org, or a non-container provider). No-op when the flag is
    off, the org is shared, or no pool is being assigned (clearing a binding
    is always allowed — the dispatch gate catches an unresolved pool)."""
    if not settings.multi_tenancy_enabled or not org_id or not pool_id:
        return
    with run_as_system():
        org = await session.get(Organization, org_id)
        if org is None or org.execution_isolation != "dedicated_pool":
            return
        pool = await session.get(RunnerPool, pool_id)
    if pool is None or pool.org_id != org_id or pool.provider not in QUALIFYING_PROVIDERS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This organization requires isolated execution: only its own "
            "docker/kubernetes runner pools can be assigned.",
        )
