"""Tests for file reading nodes (read_text_file, read_csv_file, read_json_file, read_xml_file,
read_s3_file, read_url_file, stream_large_file)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401 — registers nodes
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle_nodes.file_nodes import (
    read_csv_file,
    read_json_file,
    read_text_file,
    read_xml_file,
)


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(tok_n)
    artifact_store.reset(tok_a)

# ---------------------------------------------------------------------------
# read_text_file
# ---------------------------------------------------------------------------


def test_read_text_file_from_path(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world\nline 2", encoding="utf-8")
    result = read_text_file(input=None, path=str(f))
    assert result["text"] == "hello world\nline 2"
    assert result["filename"] == "hello.txt"
    assert result["size_bytes"] == f.stat().st_size


def test_read_text_file_encoding(tmp_path: Path) -> None:
    f = tmp_path / "latin.txt"
    f.write_bytes("caf\xe9".encode("latin-1"))
    result = read_text_file(input=None, path=str(f), encoding="latin-1")
    assert result["text"] == "café"


def test_read_text_file_no_source() -> None:
    with pytest.raises(ValueError, match="either path or file"):
        read_text_file(input=None, path="", file="")


def test_read_text_file_from_upload() -> None:
    with patch(
        "noodle_nodes.file_nodes._read_upload_bytes",
        return_value=(b"uploaded content", "notes.txt"),
    ):
        result = read_text_file(input=None, file="abc123")
    assert result["text"] == "uploaded content"
    assert result["filename"] == "notes.txt"
    assert result["size_bytes"] == len(b"uploaded content")


# ---------------------------------------------------------------------------
# read_csv_file
# ---------------------------------------------------------------------------

CSV_TEXT = "name,age\nAlice,30\nBob,25\n"


def test_read_csv_raw_mode(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text(CSV_TEXT, encoding="utf-8")
    result = read_csv_file(input=None, path=str(f), output_as_dataset=False)
    assert result["row_count"] == 2
    assert result["rows"][0] == {"name": "Alice", "age": "30"}
    assert result["rows"][1] == {"name": "Bob", "age": "25"}
    assert result["filename"] == "data.csv"


def test_read_csv_no_header(tmp_path: Path) -> None:
    f = tmp_path / "noheader.csv"
    f.write_text("a,b\nc,d\n", encoding="utf-8")
    result = read_csv_file(input=None, path=str(f), has_header=False, output_as_dataset=False)
    assert result["rows"][0] == {0: "a", 1: "b"}


def test_read_csv_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.csv"
    f.write_text("", encoding="utf-8")
    result = read_csv_file(input=None, path=str(f), output_as_dataset=False)
    assert result["rows"] == []
    assert result["row_count"] == 0


def test_read_csv_as_dataset(tmp_path: Path) -> None:
    fake_ref = {"__noodle_dataset__": True, "version": 1}
    f = tmp_path / "data.csv"
    f.write_text(CSV_TEXT, encoding="utf-8")
    with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref) as mock_parse:
        result = read_csv_file(input=None, path=str(f), output_as_dataset=True)
    assert result is fake_ref
    # csv_parse should receive the raw CSV text
    call_kwargs = mock_parse.call_args
    assert "text" in call_kwargs.kwargs or len(call_kwargs.args) >= 2


def test_read_csv_no_source() -> None:
    with pytest.raises(ValueError, match="either path or file"):
        read_csv_file(input=None, path="", file="")


# ---------------------------------------------------------------------------
# read_json_file
# ---------------------------------------------------------------------------

JSON_ARRAY = json.dumps([{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
JSON_OBJECT = json.dumps({"key": "value", "nested": [1, 2]})


def test_read_json_raw_array(tmp_path: Path) -> None:
    f = tmp_path / "arr.json"
    f.write_text(JSON_ARRAY, encoding="utf-8")
    result = read_json_file(input=None, path=str(f), output_as_dataset=False)
    assert result["data"] == [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]
    assert result["filename"] == "arr.json"


def test_read_json_raw_object(tmp_path: Path) -> None:
    f = tmp_path / "obj.json"
    f.write_text(JSON_OBJECT, encoding="utf-8")
    result = read_json_file(input=None, path=str(f))
    assert result["data"]["key"] == "value"


def test_read_json_as_dataset(tmp_path: Path) -> None:
    fake_ref = {"__noodle_dataset__": True, "version": 1}
    f = tmp_path / "arr.json"
    f.write_text(JSON_ARRAY, encoding="utf-8")
    with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref):
        result = read_json_file(input=None, path=str(f), output_as_dataset=True)
    assert result is fake_ref


def test_read_json_as_dataset_requires_array(tmp_path: Path) -> None:
    f = tmp_path / "obj.json"
    f.write_text(JSON_OBJECT, encoding="utf-8")
    with pytest.raises(ValueError, match="array of objects"):
        read_json_file(input=None, path=str(f), output_as_dataset=True)


def test_read_json_no_source() -> None:
    with pytest.raises(ValueError, match="either path or file"):
        read_json_file(input=None, path="", file="")


# ---------------------------------------------------------------------------
# read_xml_file
# ---------------------------------------------------------------------------

XML_TEXT = textwrap.dedent("""\
    <?xml version="1.0"?>
    <people>
      <person id="1">
        <name>Alice</name>
        <age>30</age>
      </person>
      <person id="2">
        <name>Bob</name>
        <age>25</age>
      </person>
    </people>
""")

UNSAFE_XML = """\
<?xml version="1.0"?>
<!DOCTYPE people [
  <!ENTITY secret SYSTEM "file:///etc/passwd">
]>
<people><person><name>&secret;</name></person></people>
"""


def test_read_xml_raw(tmp_path: Path) -> None:
    f = tmp_path / "data.xml"
    f.write_text(XML_TEXT, encoding="utf-8")
    result = read_xml_file(input=None, path=str(f), output_as_dataset=False)
    assert "people" in result["data"]
    assert result["filename"] == "data.xml"


def test_read_xml_as_dataset(tmp_path: Path) -> None:
    fake_ref = {"__noodle_dataset__": True, "version": 1}
    f = tmp_path / "data.xml"
    f.write_text(XML_TEXT, encoding="utf-8")
    with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref) as mock_parse:
        result = read_xml_file(
            input=None, path=str(f), row_xpath="person", output_as_dataset=True
        )
    assert result is fake_ref
    # Both persons should have been extracted — csv_parse gets 2-row CSV
    call_text = mock_parse.call_args.kwargs.get("text", "")
    assert "Alice" in call_text
    assert "Bob" in call_text


def test_read_xml_dataset_requires_row_xpath(tmp_path: Path) -> None:
    f = tmp_path / "data.xml"
    f.write_text(XML_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="row_xpath is required"):
        read_xml_file(input=None, path=str(f), output_as_dataset=True)


def test_read_xml_dataset_rejects_unsafe_entities(tmp_path: Path) -> None:
    f = tmp_path / "unsafe.xml"
    f.write_text(UNSAFE_XML, encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe XML"):
        read_xml_file(
            input=None,
            path=str(f),
            row_xpath="person",
            output_as_dataset=True,
        )


def test_read_xml_no_source() -> None:
    with pytest.raises(ValueError, match="either path or file"):
        read_xml_file(input=None, path="", file="")


# ---------------------------------------------------------------------------
# read_s3_file
# ---------------------------------------------------------------------------


def _make_s3_creds(endpoint: str = "") -> dict:
    return {
        "access_key_id": "AKIATEST",
        "secret_access_key": "secrettest",
        "endpoint_url": endpoint,
        "region": "us-east-1",
    }


def _mock_boto3_client(content_bytes: bytes):
    """Return a mock boto3 client whose get_object yields content_bytes."""
    body = MagicMock()
    body.read.return_value = content_bytes
    client = MagicMock()
    client.get_object.return_value = {"Body": body}
    return client


def test_read_s3_csv_raw_mode() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    csv_bytes = b"name,age\nAlice,30\nBob,25\n"
    mock_client = _mock_boto3_client(csv_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="my-bucket",
            key="data/people.csv",
            format="csv",
            output_as_dataset=False,
        )
    assert result["row_count"] == 2
    assert result["rows"][0] == {"name": "Alice", "age": "30"}
    assert result["filename"] == "people.csv"


def test_read_s3_csv_as_dataset() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    fake_ref = {"__noodle_dataset__": True}
    csv_bytes = b"name,age\nAlice,30\n"
    mock_client = _mock_boto3_client(csv_bytes)
    with patch("boto3.client", return_value=mock_client):
        with patch("noodle_nodes.file_nodes.csv_parse", return_value=fake_ref) as mock_parse:
            result = read_s3_file(
                input=None,
                credentials=_make_s3_creds(),
                bucket="b",
                key="f.csv",
                format="csv",
                output_as_dataset=True,
            )
    assert result is fake_ref
    mock_parse.assert_called_once()


def test_read_s3_json_raw_mode() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    data = [{"x": 1}, {"x": 2}]
    json_bytes = json.dumps(data).encode()
    mock_client = _mock_boto3_client(json_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="data.json",
            format="json",
            output_as_dataset=False,
        )
    assert result["data"] == data
    assert result["filename"] == "data.json"


def test_read_s3_json_as_dataset() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    fake_ref = {"__noodle_dataset__": True}
    data = [{"x": 1}, {"x": 2}]
    json_bytes = json.dumps(data).encode()
    mock_client = _mock_boto3_client(json_bytes)
    with patch("boto3.client", return_value=mock_client):
        with patch("noodle_nodes.file_nodes._parse_file_bytes", return_value=fake_ref) as mock_parse:
            result = read_s3_file(
                input=None,
                credentials=_make_s3_creds(),
                bucket="b",
                key="data.json",
                format="json",
                output_as_dataset=True,
            )
    assert result is fake_ref
    mock_parse.assert_called_once()


def test_read_s3_text_format() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    mock_client = _mock_boto3_client(b"hello world")
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="notes.txt",
            format="text",
            output_as_dataset=False,
        )
    assert result["text"] == "hello world"
    assert result["filename"] == "notes.txt"


def test_read_s3_empty_endpoint_passes_none_to_boto3() -> None:
    from noodle_nodes.file_nodes import read_s3_file

    mock_client = _mock_boto3_client(b"a,b\n1,2\n")
    with patch("boto3.client", return_value=mock_client) as mock_boto3:
        with patch("noodle_nodes.file_nodes.csv_parse", return_value={}):
            read_s3_file(
                input=None,
                credentials=_make_s3_creds(endpoint=""),
                bucket="b",
                key="f.csv",
                format="csv",
                output_as_dataset=True,
            )
    call_kwargs = mock_boto3.call_args.kwargs
    assert call_kwargs["endpoint_url"] is None


def test_read_s3_parquet_as_dataset(store_ctx, tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.file_nodes import read_s3_file

    parquet_file = tmp_path / "sample.parquet"
    table = pa.table({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
    pq.write_table(table, str(parquet_file))
    parquet_bytes = parquet_file.read_bytes()

    mock_client = _mock_boto3_client(parquet_bytes)
    with patch("boto3.client", return_value=mock_client):
        result = read_s3_file(
            input=None,
            credentials=_make_s3_creds(),
            bucket="b",
            key="data/sample.parquet",
            format="parquet",
            output_as_dataset=True,
        )
    assert is_dataset_ref(result)
    assert result["row_count"] == 3
