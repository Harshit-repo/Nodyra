"""Dataset-aware input expansion and output auto-promotion for engine nodes."""

from typing import Any

# Node types whose outputs the engine will inspect and auto-promote heavy
# values (DataFrames, large row lists, big bytes/text) into Dataset/Artifact
# refs before the output-size cap is applied. Used for Code-like nodes where
# the user might intentionally produce table-shaped data.
AUTO_PROMOTE_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Node types that handle DatasetRefs natively and therefore should *not* have
# their generic inputs auto-expanded into rows. The Code node can call dataset
# SDK helpers on the ref directly, and dataset_* nodes declare their ports as
# ``dataset`` (which are skipped anyway). Every other node receiving a
# DatasetRef on an ``any`` port gets the rows materialized so per-item logic
# (Loop Over Items, Filter, Edit Fields, …) works as authored.
DATASET_PASSTHROUGH_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Hard cap on rows the engine will expand inline from a DatasetRef before a
# generic node runs. Beyond this the node errors with guidance to reduce rows
# upstream — datasets are meant to stay artifact-backed, not materialized whole.
DATASET_AUTO_EXPAND_CAP: int = 50_000


def _auto_expand_dataset_inputs(
    node_def: Any,
    kwargs: dict[str, Any],
    node_type: str,
) -> None:
    """Expand DatasetRef inputs into rows for generic per-item nodes.

    A DatasetRef is a single envelope dict. Without this, item-processing
    nodes (Loop Over Items, Filter, …) would treat the whole dataset as one
    item and run once. Here we materialize the rows so those nodes operate on
    the records. Dataset-native node types (``DATASET_PASSTHROUGH_NODE_TYPES``)
    and ports explicitly declared as ``dataset``/``artifact`` are left as the
    raw ref.
    """
    if node_type in DATASET_PASSTHROUGH_NODE_TYPES:
        return
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind != "any" or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if is_dataset_ref(value):
            kwargs[port.name] = materialize_dataset_rows(
                value, cap=DATASET_AUTO_EXPAND_CAP
            )


def _auto_promote_outputs(
    outputs: dict[str, Any],
    *,
    max_inline_rows: int = 1000,
    max_inline_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Promote large/typed values from auto-mode nodes to Dataset/Artifact refs.

    Only triggers when the value is clearly heavy (DataFrame, big row list,
    big bytes/text). Refs that exist already, small inline values, and
    control-shaped dicts are returned unchanged.
    """
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    try:
        from noodle.dataset_promote import promote_value
    except ImportError:
        return outputs

    promoted: dict[str, Any] = {}
    for name, value in outputs.items():
        if value is None or is_dataset_ref(value) or is_artifact_ref(value):
            promoted[name] = value
            continue
        promoted[name] = promote_value(
            value,
            port_name=name,
            max_inline_rows=max_inline_rows,
            max_inline_bytes=max_inline_bytes,
        )
    return promoted
