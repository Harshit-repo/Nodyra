"""Request-scoped tenant context + the ORM enforcement layer (Layer 2).

Two enforcement layers keep tenants apart (see docs/multi-tenancy-plan.md A3):

* **Layer 2 (this module, all backends):** a ``do_orm_execute`` hook appends
  ``org_id = :current_org`` to every ORM SELECT against any model that has an
  ``org_id`` column (discovered from the mapper registry, so new org-scoped
  models are covered automatically).
* **Layer 1 (Postgres only):** an ``after_begin`` hook sets the
  ``app.current_org`` GUC with transaction scope (``set_config(..., true)``)
  so the RLS policies enforce isolation even for raw SQL that bypasses the
  ORM. ``SET LOCAL`` semantics are deliberate — a pooled connection can never
  leak the previous request's org because the setting dies with the
  transaction.

With ``multi_tenancy_enabled`` off, ``active_org_id()`` is ``None`` and both
hooks are no-ops: single-tenant behaviour is bit-for-bit unchanged.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session, with_loader_criteria

from app.config import settings

DEFAULT_ORG_ID = "default"

# Set per request by the org-resolution dependency (see app.security). Each
# asyncio task gets its own context, so concurrent requests can't see each
# other's value.
current_org_id: ContextVar[str | None] = ContextVar("current_org_id", default=None)


# Explicit opt-out for cross-org background services (queue dispatch loop,
# scheduler, retention sweep): with multi-tenancy on, an unset context falls
# back to the *default org* (fail-closed for request paths), so loops that
# legitimately operate across all orgs must declare it via run_as_system().
SYSTEM_CONTEXT = "__system__"


async def assert_safe_postgres_role(engine: AsyncEngine) -> None:
    """Fail closed when multi-tenancy would run through an RLS-bypass role."""
    if not settings.multi_tenancy_enabled or engine.dialect.name != "postgresql":
        return
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    if bool(row.rolsuper) or bool(row.rolbypassrls):
        raise RuntimeError(
            "multi-tenancy requires a PostgreSQL application role with "
            "NOSUPERUSER and NOBYPASSRLS; the configured role bypasses row-level security"
        )


def active_org_id() -> str | None:
    """The org every data access in this task must be scoped to.

    ``None`` means "no scoping": multi-tenancy disabled, or an explicit
    ``run_as_system()`` scope. With the flag on, an unset context falls back
    to the default org rather than to no filtering, so a missed
    ``resolve_org`` can never widen access.
    """
    if not settings.multi_tenancy_enabled:
        return None
    value = current_org_id.get()
    if value == SYSTEM_CONTEXT:
        return None
    return value or DEFAULT_ORG_ID


@contextmanager
def run_as_system():
    """Run a block unscoped (all orgs). For background services only —
    never call from a request handler."""
    token = current_org_id.set(SYSTEM_CONTEXT)
    try:
        yield
    finally:
        current_org_id.reset(token)


@contextmanager
def run_as_org(org_id: str | None):
    """Run a block scoped to a specific org.

    For background/unauthenticated paths (e.g. provider webhook callbacks) that
    have resolved which tenant a piece of work belongs to and must scope all
    ORM access to it. ``None`` is a no-op (keeps the ambient context)."""
    if org_id is None:
        yield
        return
    token = current_org_id.set(org_id)
    try:
        yield
    finally:
        current_org_id.reset(token)


# Memoized result of org_scoped_models().  The mapper registry is immutable
# after startup in production; tests that dynamically register models call
# ``reset_org_scoped_models()`` to force a fresh compute.
_scoped_models_cache: list[type] | None = None


def reset_org_scoped_models() -> None:
    """Clear the scoped-models cache so the next SELECT picks up models
    registered after the initial compute.  Only needed in tests."""
    global _scoped_models_cache
    _scoped_models_cache = None


def org_scoped_models() -> list[type]:
    """Every mapped class carrying an ``org_id`` column.

    Computed once on first call (the mapper registry is effectively
    immutable after startup).  Tests call :func:`reset_org_scoped_models`
    after dynamically registering models.
    """
    global _scoped_models_cache
    if _scoped_models_cache is not None:
        return _scoped_models_cache
    from app.db import Base  # late import: db imports tenancy at engine setup

    models = [
        mapper.class_
        for mapper in Base.registry.mappers
        if "org_id" in mapper.columns
    ]
    _scoped_models_cache = models
    return models


def stamp(obj: object) -> object:
    """Set ``org_id`` on a new ORM object from the request context.

    Call at every creation site of an org-scoped model. No-op when the model
    has no ``org_id`` or when it is already set explicitly.
    """
    if hasattr(obj, "org_id") and getattr(obj, "org_id", None) is None:
        org_id = active_org_id()
        obj.org_id = org_id if org_id is not None else DEFAULT_ORG_ID
    return obj


_installed = False


def install_org_filter() -> None:
    """Register both enforcement hooks once, process-wide.

    Listening on the ``Session`` class (not one sessionmaker) covers the app
    factory in ``app.db`` and any test-local sessionmaker alike.
    """
    global _installed
    if _installed:
        return
    _installed = True

    @event.listens_for(Session, "do_orm_execute")
    def _scope_selects_to_org(execute_state) -> None:
        org_id = active_org_id()
        if org_id is None or not execute_state.is_select:
            return
        # Explicit, per-query escape for legitimate cross-org reads (e.g.
        # "list MY orgs" joins memberships across orgs). Postgres RLS still
        # applies underneath — this only lifts the ORM-layer criteria.
        if execute_state.execution_options.get("skip_org_filter"):
            return
        for model in org_scoped_models():
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.org_id == org_id,  # noqa: B023 - org_id fixed per event
                    include_aliases=True,
                )
            )

    @event.listens_for(Session, "before_flush")
    def _stamp_new_org_rows(session, flush_context, instances) -> None:
        # Systematic stamping: every new org-scoped object gets its org_id
        # from the request context at flush time, so no creation site can
        # forget. Flag off -> everything lands in the default org.
        for obj in session.new:
            stamp(obj)

    @event.listens_for(Session, "after_begin")
    def _set_postgres_guc(session, transaction, connection) -> None:
        org_id = active_org_id()
        if org_id is None or connection.dialect.name != "postgresql":
            return
        # set_config(..., is_local=true) == SET LOCAL: dies with the
        # transaction, so pooled connections can't carry it across requests.
        connection.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": org_id},
        )
