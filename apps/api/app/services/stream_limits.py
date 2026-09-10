"""Line-buffer size for the newline-framed JSON subprocess protocols.

Two workers speak it — the runtime pool that executes nodes, and the
expression-preview worker — and both send a whole result as a single line.
asyncio's StreamReader defaults to a 64 KiB buffer and raises

    ValueError: Separator is not found, and chunk exceed the limit

past it, naming neither the node nor the size nor the setting. That ceiling
sat well under what the engine itself permits (``max_output_bytes``), so an
ordinary large result killed the run.

Kept in one place so the two spawn sites cannot drift apart.
"""

from __future__ import annotations

from app.config import settings

#: Floor for the buffer regardless of how small the output cap is configured.
MIN_STREAM_LIMIT_BYTES: int = 8 * 1024 * 1024

#: Headroom over the output cap for the JSON envelope and string escaping —
#: a value at the cap serializes larger than the cap once escaped.
STREAM_LIMIT_HEADROOM: int = 8


def stream_limit_bytes() -> int:
    """Bytes a single protocol line may reach before the reader gives up."""
    configured = int(getattr(settings, "max_output_bytes", 0) or 0)
    return max(MIN_STREAM_LIMIT_BYTES, configured * STREAM_LIMIT_HEADROOM)
