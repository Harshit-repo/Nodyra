"""Tests for document intelligence nodes."""

from __future__ import annotations

import io
import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="doc-intel-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("doc-intel-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def test_pdf_extract_text_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import pdf_extract_text
    with pytest.raises(ValueError, match="input is required"):
        pdf_extract_text(input=None)


def test_pdf_extract_text_raises_missing_package(store_ctx, monkeypatch) -> None:
    """Lazy import raises RuntimeError with install instructions when pdfplumber absent."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pdfplumber":
            raise ImportError("No module named 'pdfplumber'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.document_intelligence import pdf_extract_text
    # Write a fake artifact so we pass the None check
    from noodle.artifacts import write_bytes
    fake_ref = write_bytes(b"fake", name="test.pdf", content_type="application/pdf")
    with pytest.raises(RuntimeError, match="pdfplumber"):
        pdf_extract_text(input=fake_ref)


def test_pdf_extract_text_raises_for_scanned_pdf(store_ctx) -> None:
    """PDF with no text layer raises ValueError with helpful message."""
    pytest.importorskip("pdfplumber")
    from unittest.mock import MagicMock, patch
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import pdf_extract_text

    fake_ref = write_bytes(b"fake", name="test.pdf", content_type="application/pdf")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""
    mock_page.page_number = 1

    mock_pdf_ctx = MagicMock()
    mock_pdf_ctx.__enter__ = lambda s: s
    mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
    mock_pdf_ctx.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf_ctx):
        with pytest.raises(ValueError, match="scanned"):
            pdf_extract_text(input=fake_ref)


def test_pdf_extract_text_returns_text_and_artifact(store_ctx) -> None:
    pytest.importorskip("pdfplumber")
    from unittest.mock import MagicMock, patch
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import pdf_extract_text

    fake_ref = write_bytes(b"fake", name="test.pdf", content_type="application/pdf")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Hello world this is page one."
    mock_page.page_number = 1

    mock_pdf_ctx = MagicMock()
    mock_pdf_ctx.__enter__ = lambda s: s
    mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
    mock_pdf_ctx.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf_ctx):
        result = pdf_extract_text(input=fake_ref)

    assert "Hello world" in result["text"]
    assert result["pages"] == 1
    assert result["pages_extracted"] == 1
    assert is_artifact_ref(result["artifact"])


def test_pdf_extract_tables_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import pdf_extract_tables
    with pytest.raises(ValueError, match="input is required"):
        pdf_extract_tables(input=None)


def test_pdf_extract_tables_no_tables_returns_zero(store_ctx) -> None:
    pytest.importorskip("pdfplumber")
    pytest.importorskip("pandas")
    from unittest.mock import MagicMock, patch
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import pdf_extract_tables

    fake_ref = write_bytes(b"fake", name="test.pdf", content_type="application/pdf")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Some text but no tables"
    mock_page.extract_tables.return_value = []

    mock_pdf_ctx = MagicMock()
    mock_pdf_ctx.__enter__ = lambda s: s
    mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
    mock_pdf_ctx.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf_ctx):
        result = pdf_extract_tables(input=fake_ref)

    assert result["tables_found"] == 0
    assert result["dataset"] is None


def test_pdf_extract_tables_returns_dataset_ref(store_ctx) -> None:
    pytest.importorskip("pdfplumber")
    pytest.importorskip("pandas")
    from unittest.mock import MagicMock, patch
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import pdf_extract_tables

    fake_ref = write_bytes(b"fake", name="test.pdf", content_type="application/pdf")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Some text"
    mock_page.extract_tables.return_value = [
        [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
    ]

    mock_pdf_ctx = MagicMock()
    mock_pdf_ctx.__enter__ = lambda s: s
    mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
    mock_pdf_ctx.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf_ctx):
        result = pdf_extract_tables(input=fake_ref)

    assert result["tables_found"] == 1
    assert is_dataset_ref(result["dataset"])
    assert result["summary"][0]["rows"] == 2
    assert result["summary"][0]["columns"] == 2


def test_parse_page_selection_blank_returns_all(store_ctx) -> None:
    from noodle_nodes.document_intelligence import _parse_page_selection
    assert _parse_page_selection("", 5, 0) == [0, 1, 2, 3, 4]


def test_parse_page_selection_range(store_ctx) -> None:
    from noodle_nodes.document_intelligence import _parse_page_selection
    assert _parse_page_selection("1-3", 5, 0) == [0, 1, 2]


def test_parse_page_selection_list(store_ctx) -> None:
    from noodle_nodes.document_intelligence import _parse_page_selection
    assert _parse_page_selection("1,3,5", 5, 0) == [0, 2, 4]


def test_document_intelligence_importable_without_optional_packages() -> None:
    """Module must import cleanly even when no doc-processing packages are installed."""
    import importlib
    mod = importlib.import_module("noodle_nodes.document_intelligence")
    assert hasattr(mod, "pdf_extract_text")
    assert hasattr(mod, "pdf_extract_tables")
    assert hasattr(mod, "pdf_generate")
    assert hasattr(mod, "docx_generate")
    assert hasattr(mod, "excel_report_generate")
    assert hasattr(mod, "docx_extract")
    assert hasattr(mod, "excel_extract")
    assert hasattr(mod, "barcode_qr_generate")
    assert hasattr(mod, "barcode_qr_decode")
