from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from noodle.serialization import (
    TYPED_MARKER,
    deserialize_value,
    serialize_value,
    truncate_serialized_value,
)


def test_serializes_and_deserializes_scalar_types() -> None:
    value = {
        "created": datetime(2026, 5, 25, 12, 30, tzinfo=UTC),
        "day": date(2026, 5, 25),
        "at": time(9, 45, 12),
        "price": Decimal("19.9900"),
        "raw": b"hello",
        "mutable_raw": bytearray(b"world"),
    }

    serialized = serialize_value(value)

    assert serialized["created"]["type"] == "datetime"
    assert serialized["price"]["type"] == "decimal"
    assert serialized["raw"]["type"] == "bytes"

    restored = deserialize_value(serialized)
    assert restored == value
    assert isinstance(restored["created"], datetime)
    assert isinstance(restored["price"], Decimal)
    assert isinstance(restored["raw"], bytes)
    assert isinstance(restored["mutable_raw"], bytearray)


def test_serializes_and_deserializes_collection_types() -> None:
    value = {
        "coords": (1, Decimal("2.5")),
        "tags": {"beta", "vip"},
        "frozen": frozenset({1, 2}),
    }

    serialized = serialize_value(value)
    assert serialized["coords"]["type"] == "tuple"
    assert serialized["tags"]["type"] == "set"
    assert serialized["frozen"]["type"] == "frozenset"

    restored = deserialize_value(serialized)
    assert restored["coords"] == (1, Decimal("2.5"))
    assert restored["tags"] == {"beta", "vip"}
    assert restored["frozen"] == frozenset({1, 2})


class DataFrame:
    def __init__(self, records: list[dict]):
        self._records = records
        self.columns = list(records[0].keys()) if records else []
        self.dtypes = {column: "object" for column in self.columns}
        self.shape = (len(records), len(self.columns))

    def head(self, rows: int) -> "DataFrame":
        return DataFrame(self._records[:rows])

    def to_dict(self, orient: str) -> list[dict]:
        assert orient == "records"
        return self._records


def test_serializes_dataframe_preview_without_importing_pandas() -> None:
    df = DataFrame(
        [
            {"id": 1, "name": "Ada"},
            {"id": 2, "name": "Grace"},
            {"id": 3, "name": "Linus"},
        ]
    )

    serialized = serialize_value(df, dataframe_max_rows=2)

    assert serialized[TYPED_MARKER] is True
    assert serialized["type"] == "dataframe"
    assert serialized["restorable"] is False
    assert serialized["value"]["columns"] == ["id", "name"]
    assert serialized["value"]["row_count"] == 3
    assert serialized["value"]["preview_row_count"] == 2
    assert serialized["value"]["records"] == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
    ]


def test_deserializes_dataframe_when_pandas_is_available() -> None:
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([{"id": 1, "name": "Ada"}])

    restored = deserialize_value(serialize_value(df))

    assert isinstance(restored, pd.DataFrame)
    assert restored.to_dict("records") == [{"id": 1, "name": "Ada"}]


def test_unknown_custom_object_is_preview_only() -> None:
    class Widget:
        def __repr__(self) -> str:
            return "Widget(id=1)"

    serialized = serialize_value({"widget": Widget()})

    widget = serialized["widget"]
    assert widget["type"] == "object"
    assert widget["restorable"] is False
    assert widget["python_type"] == "Widget"
    assert widget["repr"] == "Widget(id=1)"
    assert deserialize_value(widget) == widget


def test_nested_structures_preserve_typed_values() -> None:
    serialized = serialize_value(
        [{"value": Decimal("1.25")}, {"when": datetime(2026, 5, 25, 8, 0)}]
    )

    restored = deserialize_value(serialized)

    assert restored[0]["value"] == Decimal("1.25")
    assert restored[1]["when"] == datetime(2026, 5, 25, 8, 0)


def test_truncation_preserves_typed_envelope_shape() -> None:
    serialized = serialize_value(b"x" * 5000)
    capped = truncate_serialized_value(serialized, 512)

    assert capped[TYPED_MARKER] is True
    assert capped["type"] == "bytes"
    assert capped["restorable"] is False
    assert capped["truncated"] is True
    assert "base64" not in capped["value"]
    assert capped["value"]["byte_length"] == 5000


def test_dataset_ref_serialization_passes_through_unchanged() -> None:
    dataset_ref = {
        "__noodle_dataset__": True,
        "version": 1,
        "dataset_id": "ds_123",
        "format": "parquet",
        "row_count": 2,
        "column_count": 1,
        "schema": [{"name": "city", "type": "VARCHAR"}],
        "preview": [{"city": "NYC"}, {"city": "LA"}],
        "preview_truncated": False,
        "artifact": {
            "__noodle_artifact__": True,
            "version": 1,
            "artifact_id": "art_123",
            "run_id": "run_123",
            "node_id": "node_123",
            "name": "cities.parquet",
            "kind": "dataset",
            "content_type": "application/vnd.apache.parquet",
            "size_bytes": 1234,
            "storage_backend": "local",
            "storage_key": "runs/run_123/node_123/art_123-cities.parquet",
        },
    }

    assert serialize_value(dataset_ref) is dataset_ref


def test_dataset_ref_truncation_preserves_ref_identity() -> None:
    dataset_ref = {
        "__noodle_dataset__": True,
        "version": 1,
        "dataset_id": "ds_large",
        "format": "parquet",
        "row_count": 10_000,
        "column_count": 2,
        "schema": [
            {"name": "city", "type": "VARCHAR"},
            {"name": "description", "type": "VARCHAR"},
        ],
        "preview": [
            {"city": f"city-{i}", "description": "x" * 200}
            for i in range(50)
        ],
        "preview_truncated": True,
        "artifact": {
            "__noodle_artifact__": True,
            "version": 1,
            "artifact_id": "art_large",
            "run_id": "run_large",
            "node_id": "node_large",
            "name": "large.parquet",
            "kind": "dataset",
            "content_type": "application/vnd.apache.parquet",
            "size_bytes": 987654,
            "storage_backend": "local",
            "storage_key": "runs/run_large/node_large/art_large-large.parquet",
        },
    }

    capped = truncate_serialized_value(dataset_ref, 256)

    assert capped["__noodle_dataset__"] is True
    assert capped["dataset_id"] == "ds_large"
    assert capped["artifact"]["__noodle_artifact__"] is True
    assert capped["artifact"]["artifact_id"] == "art_large"
    assert capped["row_count"] == 10_000
    assert capped["preview_truncated"] is True
    assert capped.get("_truncated") is not True
