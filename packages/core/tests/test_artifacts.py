import json

from nodyra import artifacts
from nodyra.context import artifact_store, current_node_id, node_debug


def test_local_artifact_store_writes_and_reads_text(tmp_path) -> None:
    store = artifacts.LocalArtifactStore(tmp_path, "run1", max_bytes=100)
    store_token = artifact_store.set(store)
    node_token = current_node_id.set("nodeA")
    debug: dict = {}
    debug_token = node_debug.set(debug)
    try:
        ref = artifacts.write_text("hello", name="../hello.txt")
    finally:
        node_debug.reset(debug_token)
        current_node_id.reset(node_token)
        artifact_store.reset(store_token)

    assert ref[artifacts.ARTIFACT_MARKER] is True
    assert ref["run_id"] == "run1"
    assert ref["node_id"] == "nodeA"
    assert ref["name"] == "hello.txt"
    assert debug["artifacts"][0]["artifact_id"] == ref["artifact_id"]

    store_token = artifact_store.set(store)
    try:
        assert artifacts.read_text(ref) == "hello"
    finally:
        artifact_store.reset(store_token)


def test_write_json_keeps_payload_out_of_ref_bytes(tmp_path) -> None:
    store = artifacts.LocalArtifactStore(tmp_path, "run2")
    store_token = artifact_store.set(store)
    node_token = current_node_id.set("nodeB")
    try:
        ref = artifacts.write_json({"items": [1, 2]}, name="items.json")
    finally:
        current_node_id.reset(node_token)
        artifact_store.reset(store_token)

    assert ref["kind"] == "json"
    assert ref["size_bytes"] > 0
    assert "items" in ref["preview"]
    assert "base64" not in ref
    assert json.loads(store.read_bytes(ref)) == {"items": [1, 2]}


def test_artifact_store_enforces_size_limit(tmp_path) -> None:
    store = artifacts.LocalArtifactStore(tmp_path, "run3", max_bytes=3)
    store_token = artifact_store.set(store)
    try:
        try:
            artifacts.write_bytes(b"toolarge", name="x.bin")
        except ValueError as exc:
            assert "limit is 3 bytes" in str(exc)
        else:
            raise AssertionError("expected artifact limit failure")
    finally:
        artifact_store.reset(store_token)
