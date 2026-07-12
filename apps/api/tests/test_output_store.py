"""Unit tests for the large-output offload store (``app.services.output_store``).

Focus: the OS-1 regression where ``_validate_key`` required *both* key segments
to be 32-char hex UUIDs. Real ``node_id`` values are workflow-assigned ids
(``n_ab12_3``, ``producer``, ...), never UUIDs, so the write key never validated,
``_write_output`` raised, and ``maybe_offload_output`` silently kept every output
inline — the whole offload feature was dead code. These tests pin the corrected
contract: offload fires for real node ids, round-trips, and still refuses keys
that could escape the output root.
"""

from __future__ import annotations

import pytest

from app.services import output_store

RUN_ID = "a" * 32  # a realistic uuid4().hex run id


@pytest.fixture
def offload_root(tmp_path, monkeypatch):
    """Force a tiny inline threshold and a temp output root."""
    monkeypatch.setattr(output_store, "_INLINE_THRESHOLD_BYTES", 16)
    monkeypatch.setattr(output_store, "_output_root", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "node_id",
    ["n_lkj2h3_0", "producer", "manual_trigger", "b" * 32, "Node-1.step_2"],
)
def test_offload_fires_for_real_node_ids(offload_root, node_id):
    """OS-1: a large output under a realistic node_id must offload to a marker,
    not silently stay inline. Regression for the hex-only ``_validate_key``."""
    big = {"main": list(range(500))}
    result = output_store.maybe_offload_output(big, run_id=RUN_ID, node_id=node_id)

    assert isinstance(result, dict)
    assert set(result) == {"__output_ref"}
    assert result["__output_ref"] == f"{RUN_ID}/{node_id}"
    # The marker resolves back to the exact original value.
    assert output_store.resolved_output(result) == big
    # And it was actually written to disk.
    assert (offload_root / f"{RUN_ID}/{node_id}.json").exists()


def test_small_output_stays_inline(offload_root):
    small = {"main": 1}
    assert output_store.maybe_offload_output(small, run_id=RUN_ID, node_id="producer") is small


def test_none_passes_through(offload_root):
    assert output_store.maybe_offload_output(None, run_id=RUN_ID, node_id="producer") is None
    assert output_store.resolved_output(None) is None


@pytest.mark.parametrize(
    "key",
    [
        f"{RUN_ID}/..",            # parent-dir escape
        f"{RUN_ID}/../secrets",    # traversal
        f"{RUN_ID}/.",             # current dir
        f"{RUN_ID}/a/b",           # extra separator
        f"{RUN_ID}/",              # empty node id
        "not-a-uuid/producer",     # non-hex run id
        f"{RUN_ID}/node id",       # space (not an identifier char)
        f"{RUN_ID}/{'x' * 200}",   # over length bound
    ],
)
def test_validate_key_rejects_unsafe_keys(key):
    assert output_store._validate_key(key) is False


@pytest.mark.parametrize(
    "key",
    [f"{RUN_ID}/n_lkj2h3_0", f"{RUN_ID}/producer", f"{RUN_ID}/{'b' * 32}"],
)
def test_validate_key_accepts_well_formed_keys(key):
    assert output_store._validate_key(key) is True


def test_tampered_marker_does_not_escape_root(offload_root):
    """A marker whose key points outside the output root must NOT be read;
    ``resolved_output`` returns the marker unchanged rather than reading the
    traversal path."""
    tampered = {"__output_ref": f"{RUN_ID}/../../etc/passwd"}
    assert output_store.resolved_output(tampered) == tampered


def test_delete_outputs_for_run_ids_removes_run_directory(offload_root):
    """Phase 3.1: GC must remove the whole <run_id>/ directory so retention
    pruning doesn't leak offloaded files after the NodeRun rows are gone."""
    output_store.maybe_offload_output(
        {"main": list(range(500))}, run_id=RUN_ID, node_id="producer"
    )
    run_dir = offload_root / RUN_ID
    assert run_dir.exists()

    output_store.delete_outputs_for_run_ids([RUN_ID])

    assert not run_dir.exists()


def test_delete_outputs_for_run_ids_ignores_malformed_and_missing(offload_root):
    """Non-hex ids can't match a real offload directory (see _validate_key) —
    must be skipped rather than raise; a missing directory is also a no-op."""
    output_store.delete_outputs_for_run_ids(["not-a-uuid", "b" * 32])  # no raise


def _counter_value(counter, **labels) -> int:
    """Parse a specific label combination's count out of the Prometheus text
    exposition, since ``_Counter`` only exposes ``inc()``/``render()``."""
    target = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    for line in counter.render().splitlines():
        if line.startswith("#"):
            continue
        if f"{{{target}}}" in line:
            return int(line.rsplit(" ", 1)[1])
    return 0


def test_write_failure_increments_metric_and_falls_back_inline(offload_root, monkeypatch):
    """Phase 1.2: a disk/backend failure on write must be observable (counter +
    log), not silent — the run still succeeds inline, but a sustained rate
    should be alertable rather than invisible."""
    from app.services.metrics import output_store_events_total

    def boom(key, raw):
        raise OSError("disk full")

    monkeypatch.setattr(output_store, "_write_output", boom)
    before = _counter_value(output_store_events_total, event="write_failed")

    big = {"main": list(range(500))}
    result = output_store.maybe_offload_output(big, run_id=RUN_ID, node_id="producer")

    assert result is big  # falls back to inline, run is unaffected
    after = _counter_value(output_store_events_total, event="write_failed")
    assert after == before + 1


def test_read_failure_increments_metric_and_returns_marker(offload_root, monkeypatch):
    """Phase 1.2: a read failure (corrupted/missing offload file) must be
    observable and degrade to returning the marker rather than raising."""
    from app.services.metrics import output_store_events_total

    def boom(key):
        raise OSError("file missing")

    monkeypatch.setattr(output_store, "_read_output", boom)
    before = _counter_value(output_store_events_total, event="read_failed")

    marker = {"__output_ref": f"{RUN_ID}/producer"}
    result = output_store.maybe_load_output(marker)

    assert result == marker  # degrades to the marker, doesn't raise
    after = _counter_value(output_store_events_total, event="read_failed")
    assert after == before + 1
