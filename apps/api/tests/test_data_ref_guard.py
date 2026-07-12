"""Static guard (OS-1/OS-2 regression): every read of ``NodeRun.output``
under ``app`` must go through :func:`app.services.data_ref.resolve_ref` (or
one of the ``output_store`` functions it wraps).

Before OS-1/OS-2 were fixed, ten call sites read ``NodeRun.output`` directly
and would receive the raw ``{"__output_ref": key}`` offload marker instead of
the resolved value — retried/replayed nodes were silently skipped, and the
UI/MCP/webhook surfaces showed the marker. This test parses every module
under ``app`` and fails if a new unwrapped ``.output`` read appears, so that
class of bug can't be silently reintroduced by a future call site.

This is a structural/static check, not a runtime one — it complements (does
not replace) the behavioral regression tests in ``test_runs.py`` and
``test_output_store.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Names that resolve a stored reference to its real value. A call to one of
# these "guards" every ``.output`` attribute read nested inside its arguments.
_RESOLVER_NAMES = frozenset({"resolve_ref", "resolved_output", "maybe_load_output"})

# These modules implement the resolvers themselves (or re-export them) and are
# reviewed separately — they're exempt from the "must be wrapped" rule.
_EXEMPT_RELPATHS = frozenset({"services/output_store.py", "services/data_ref.py"})

# `NodeRun.output` used as a class attribute (e.g. inside `select(...)` or
# `.order_by(...)`) builds a SQL expression — it never yields a Python value,
# so it isn't a "read" that needs resolving.
_EXEMPT_CLASS_NAMES = frozenset({"NodeRun"})


class _UnresolvedOutputReadFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[int] = []
        self._guard_depth = 0

    @staticmethod
    def _call_name(func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        guarded = self._call_name(node.func) in _RESOLVER_NAMES
        if guarded:
            self._guard_depth += 1
        self.generic_visit(node)
        if guarded:
            self._guard_depth -= 1

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        # Existence checks like `nr.output is not None` don't consume the
        # value, so `.output` reads directly inside a comparison are exempt.
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.Attribute)
                and child.attr == "output"
                and isinstance(child.ctx, ast.Load)
            ):
                continue
            self.visit(child)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        is_class_ref = (
            isinstance(node.value, ast.Name) and node.value.id in _EXEMPT_CLASS_NAMES
        )
        if (
            node.attr == "output"
            and isinstance(node.ctx, ast.Load)
            and self._guard_depth == 0
            and not is_class_ref
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)


def _find_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(API_APP_ROOT.rglob("*.py")):
        rel = path.relative_to(API_APP_ROOT).as_posix()
        if rel in _EXEMPT_RELPATHS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        finder = _UnresolvedOutputReadFinder()
        finder.visit(tree)
        violations.extend(f"{rel}:{lineno}" for lineno in finder.violations)
    return violations


def test_no_unresolved_node_run_output_reads() -> None:
    violations = _find_violations()
    assert not violations, (
        "Found unwrapped NodeRun.output read(s) that bypass resolve_ref — "
        "an offloaded output would surface as a raw {'__output_ref': ...} "
        f"marker instead of its value: {violations}"
    )


def test_guard_detects_an_injected_unwrapped_read(tmp_path) -> None:
    """Proves the checker isn't vacuously passing: an unwrapped `.output`
    read must be flagged when parsed directly (not scanned from disk, so
    this doesn't touch the real ``app`` tree)."""
    src = "async def bad_reader(nr):\n    return nr.output\n"
    tree = ast.parse(src, filename="<injected>")
    finder = _UnresolvedOutputReadFinder()
    finder.visit(tree)
    assert finder.violations == [2]
