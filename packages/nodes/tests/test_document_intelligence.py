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
