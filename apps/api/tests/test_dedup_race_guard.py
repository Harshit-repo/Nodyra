"""Every de-duplicated run dispatch must survive losing the insert race.

``start_run`` with a ``deduplication_key`` can raise ``DuplicateRun``: two
concurrent deliveries of one event both pass the caller's "have I seen this
key?" read before either commits, and the unique index on
``runs.deduplication_key`` then arbitrates.

A caller that does not handle it turns a correct de-duplication into an error
response. For a webhook that is worse than it sounds — providers retry on
4xx and 5xx, so the answer to a duplicate-delivery storm becomes a request
for more of it.

This is a source-level guard rather than three integration tests because the
risk is a *fourth* call site being added later without the handler.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


def _python_files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def _handles_the_race(handlers: list[ast.ExceptHandler]) -> bool:
    """True when one of these handlers catches DuplicateRun (or everything)."""
    for handler in handlers:
        caught = handler.type
        if caught is None:  # bare except
            return True
        names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
        for name in names:
            label = (
                name.id
                if isinstance(name, ast.Name)
                else name.attr
                if isinstance(name, ast.Attribute)
                else ""
            )
            if label in {"DuplicateRun", "Exception", "BaseException", "ServiceError"}:
                return True
    return False


def _scan(source: str, label: str = "<memory>") -> list[tuple[int, bool]]:
    """Return (lineno, guarded) for each start_run(..., deduplication_key=...)."""
    tree = ast.parse(source, filename=label)
    enclosing: dict[ast.AST, list[ast.Try]] = {}

    def walk(node: ast.AST, tries: list[ast.Try]) -> None:
        enclosing[node] = tries
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Try) and child in node.body:
                walk(child, [*tries, node])
            else:
                walk(child, tries)

    walk(tree, [])

    found: list[tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "start_run":
            continue
        if not any(kw.arg == "deduplication_key" for kw in node.keywords):
            continue
        found.append(
            (node.lineno, any(_handles_the_race(t.handlers) for t in enclosing.get(node, [])))
        )
    return found


def _dedup_start_run_calls() -> list[tuple[Path, int, bool]]:
    """Locate every start_run(..., deduplication_key=...) and say if it's guarded."""
    found: list[tuple[Path, int, bool]] = []
    for path in _python_files():
        for lineno, guarded in _scan(path.read_text(encoding="utf-8"), str(path)):
            found.append((path, lineno, guarded))
    return found


UNGUARDED = """
async def dispatch():
    run_id = await start_run(wf, graph, 1, deduplication_key=key)
"""

GUARDED = """
async def dispatch():
    try:
        run_id = await start_run(wf, graph, 1, deduplication_key=key)
    except DuplicateRun:
        return acknowledged()
"""

NO_DEDUP_KEY = """
async def dispatch():
    run_id = await start_run(wf, graph, 1, mode="manual")
"""


def test_the_guard_catches_an_unguarded_call() -> None:
    """Proves the check is not vacuous: it must fail on the shape it forbids."""
    assert _scan(UNGUARDED) == [(3, False)]


def test_the_guard_accepts_a_guarded_call() -> None:
    assert _scan(GUARDED) == [(4, True)]


def test_the_guard_ignores_dispatches_with_no_dedup_key() -> None:
    """A run with no key cannot collide, so it needs no handler."""
    assert _scan(NO_DEDUP_KEY) == []


def test_the_guard_finds_the_known_call_sites() -> None:
    """If this fails the AST walk broke, not the product."""
    calls = _dedup_start_run_calls()

    assert len(calls) >= 3, (
        "expected the webhook, schedule-occurrence and provider-trigger "
        f"dispatches; found {[(str(p), n) for p, n, _ in calls]}"
    )


@pytest.mark.parametrize(
    ("path", "lineno", "guarded"),
    [pytest.param(*c, id=f"{c[0].name}:{c[1]}") for c in _dedup_start_run_calls()],
)
def test_dedup_dispatch_handles_a_lost_race(
    path: Path, lineno: int, guarded: bool
) -> None:
    assert guarded, (
        f"{path.name}:{lineno} calls start_run with a deduplication_key but does "
        "not catch DuplicateRun. Concurrent deliveries of the same event will "
        "surface the unique-constraint loser as an error instead of "
        "acknowledging the duplicate."
    )
