"""Single read-boundary chokepoint for resolving ``NodeRun.output`` references.

``NodeRun.output`` can be a plain ``{port: value}`` dict, or a reference that
needs resolving before a caller can use it:

- an output-store offload marker (``{"__output_ref": "<run_id>/<node_id>"}``,
  see :mod:`app.services.output_store`) for outputs above the inline size
  threshold.

Every API/MCP/webhook read of ``NodeRun.output`` must go through
:func:`resolve_ref` (or the ``output_store`` functions it wraps) rather than
reading the column directly. Before OS-1/OS-2 were fixed, ten call sites read
the column raw and would receive the offload marker instead of the value —
retried/replayed nodes were silently skipped, and the UI/MCP/webhook surfaces
showed the marker. ``tests/test_data_ref_guard.py`` statically enforces this:
it fails the build if a new raw read of ``.output`` is added anywhere under
``app`` without going through a resolver here.

Artifact (``__nodyra_artifact__``) and dataset (``__nodyra_dataset__``)
references are intentionally NOT resolved here — those stay as refs for the
frontend to fetch on demand (signed URL / preview), unlike an offloaded
output, which the caller expects to look and behave like the original value.
If a future reference type needs read-boundary resolution, add it here so
this stays the one place that decides what "resolve" means for a stored
output.
"""

from __future__ import annotations

from typing import Any

from app.services.output_store import resolved_output


def resolve_ref(output: Any) -> Any:  # noqa: ANN401
    """Resolve ``output`` to its real value if it is a storage reference.

    Non-reference values (including ``None``) pass through unchanged.
    """
    return resolved_output(output)


__all__ = ["resolve_ref"]
