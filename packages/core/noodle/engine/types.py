"""Shared engine types with no internal dependencies."""

from collections.abc import Awaitable, Callable
from typing import Any

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""
