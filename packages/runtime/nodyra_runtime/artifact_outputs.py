"""Stream completed files before the node event that advertises their refs."""

import base64
from typing import Any

from nodyra.artifacts import LocalArtifactStore, is_artifact_ref


def _refs(value: Any):
    if is_artifact_ref(value):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)


def output_messages(value: Any, store: LocalArtifactStore, sent: set[str]):
    for ref in _refs(value):
        if ref.get("run_id") != store.run_id or ref["artifact_id"] in sent:
            continue
        size = int(ref["size_bytes"])
        offset = 0
        with store.path_for_ref(ref).open("rb") as source:
            while True:
                chunk = source.read(256 * 1024)
                if not chunk and offset < size:
                    raise RuntimeError("Artifact file changed during transfer")
                final = offset + len(chunk) == size
                yield {
                    "type": "artifact_output",
                    "ref": ref,
                    "offset": offset,
                    "chunk": base64.b64encode(chunk).decode("ascii"),
                    "final": final,
                }
                offset += len(chunk)
                if final:
                    break
        sent.add(ref["artifact_id"])
