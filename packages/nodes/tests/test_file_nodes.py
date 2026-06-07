"""Tests for file reading nodes (read_text_file, read_csv_file, read_json_file, read_xml_file)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import noodle_nodes  # noqa: F401 — registers nodes
from noodle.sdk import registry
from noodle_nodes.file_nodes import (
    read_csv_file,
    read_json_file,
    read_text_file,
    read_xml_file,
)

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


def test_read_xml_no_source() -> None:
    with pytest.raises(ValueError, match="either path or file"):
        read_xml_file(input=None, path="", file="")
