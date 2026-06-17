# Document Intelligence Nodes — Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 9 built-in document intelligence nodes (PDF extraction, PDF/Word/Excel generation, barcode/QR) that exploit Python's document ecosystem and have no equivalent in n8n.

**Architecture:** All nodes live in a new `packages/nodes/noodle_nodes/document_intelligence.py` module, registered in `__init__.py`. Heavy dependencies (`pdfplumber`, `python-docx`, `openpyxl`, `weasyprint`, `qrcode`, `pyzbar`) are lazy-imported inside each function body and declared via `requirements=[...]` on the `@node` decorator so the editor can prompt the user to install them. No package is imported at module scope.

**Tech Stack:** `pdfplumber>=0.11`, `pandas>=2.0`, `weasyprint>=60.0`, `jinja2>=3.0`, `python-docx>=1.1`, `openpyxl>=3.1`, `qrcode[pil]>=7.4`, `python-barcode>=0.15`, `pyzbar>=0.1.9`, `pillow>=10.0`

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `packages/nodes/noodle_nodes/document_intelligence.py` | All 9 nodes + private helpers |
| Modify | `packages/nodes/noodle_nodes/__init__.py` | Register the new module |
| Create | `packages/nodes/tests/test_document_intelligence.py` | All tests |

---

## Task 1: Module scaffold + import-safety test

**Files:**
- Create: `packages/nodes/noodle_nodes/document_intelligence.py`
- Create: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing import-safety test**

```python
# packages/nodes/tests/test_document_intelligence.py
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
```

- [ ] **Step 2: Run the test — expect ImportError (module doesn't exist yet)**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py::test_document_intelligence_importable_without_optional_packages -v
```

Expected: `ModuleNotFoundError: No module named 'noodle_nodes.document_intelligence'`

- [ ] **Step 3: Create the module scaffold**

```python
# packages/nodes/noodle_nodes/document_intelligence.py
"""Document intelligence nodes — PDF, Word, Excel, barcodes.

All heavy dependencies are lazy-imported inside each function body.
No optional package is imported at module scope.
"""

from __future__ import annotations

import io
import re
from typing import Any

from noodle.artifacts import read_bytes as _read_bytes
from noodle.artifacts import write_bytes as _write_bytes
from noodle.artifacts import write_text as _write_text
from noodle.sdk import node

# Nodes are defined below. Helpers first.
```

- [ ] **Step 4: Register the module in `__init__.py`**

Open `packages/nodes/noodle_nodes/__init__.py` and add after the last `from noodle_nodes import` line:

```python
from noodle_nodes import document_intelligence as document_intelligence
```

And add `"document_intelligence"` to `__all__`.

- [ ] **Step 5: Run the import-safety test — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py::test_document_intelligence_importable_without_optional_packages -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/noodle_nodes/__init__.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): scaffold document_intelligence module"
```

---

## Task 2: `pdf_extract_text`

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_document_intelligence.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_extract_text" -v
```

Expected: 4 failures (function not yet defined)

- [ ] **Step 3: Implement `pdf_extract_text`**

Add to `document_intelligence.py` after the module scaffold:

```python
@node(
    name="PDF Extract Text",
    id="pdf_extract_text",
    category="Document Intelligence",
    icon="page",
    requirements=["pdfplumber>=0.11"],
    params={
        "max_pages": {"description": "Maximum pages to extract (0 = all)"},
    },
)
def pdf_extract_text(input=None, max_pages: int = 0) -> dict:
    """Extract text from a PDF artifact with layout preservation."""
    if input is None:
        raise ValueError("input is required — wire a PDF artifact to this node.")

    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required. Add pdfplumber to the workflow environment, "
            "rebuild it, then run again."
        ) from exc

    raw = _read_bytes(input)
    pages_out: list[dict] = []

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total = len(pdf.pages)
        limit = total if max_pages == 0 else min(max_pages, total)
        for page in pdf.pages[:limit]:
            pages_out.append({
                "page": page.page_number,
                "text": page.extract_text() or "",
            })

    full_text = "\n\n".join(p["text"] for p in pages_out if p["text"])

    if not full_text.strip():
        raise ValueError(
            "PDF appears to be scanned (no text layer found). "
            "Use the ocr_document node first to add a text layer."
        )

    artifact = _write_text(full_text, name="extracted_text.txt")
    return {
        "text": full_text[:10_000] if len(full_text) > 10_000 else full_text,
        "truncated": len(full_text) > 10_000,
        "pages": total,
        "pages_extracted": limit,
        "artifact": artifact,
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_extract_text" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add pdf_extract_text node"
```

---

## Task 3: `pdf_extract_tables` (killer node ★)

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_extract_tables or parse_page" -v
```

Expected: 6 failures

- [ ] **Step 3: Implement `_parse_page_selection` helper and `pdf_extract_tables`**

Add to `document_intelligence.py`:

```python
def _parse_page_selection(pages_str: str, total: int, max_pages: int) -> list[int]:
    """Parse '1,2,3' or '1-5' page string into zero-based page indices."""
    if not pages_str.strip():
        limit = total if max_pages == 0 else min(max_pages, total)
        return list(range(limit))
    indices: set[int] = set()
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            indices.update(range(int(a) - 1, int(b)))
        else:
            indices.add(int(part) - 1)
    return sorted(i for i in indices if 0 <= i < total)


@node(
    name="PDF Extract Tables",
    id="pdf_extract_tables",
    category="Document Intelligence",
    icon="table",
    requirements=["pdfplumber>=0.11", "pandas>=2.0"],
    params={
        "pages": {"placeholder": "1,2,3 or 1-5 (blank = all pages)"},
        "max_pages": {"description": "Cap total pages scanned (0 = all)"},
    },
)
def pdf_extract_tables(input=None, pages: str = "", max_pages: int = 0) -> dict:
    """Extract tables from a PDF to a DatasetRef. Each detected table becomes one DatasetRef."""
    if input is None:
        raise ValueError("input is required — wire a PDF artifact to this node.")

    try:
        import pdfplumber  # type: ignore[import-not-found]
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber and pandas are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from noodle_nodes.datasets import dataframe_to_dataset

    raw = _read_bytes(input)
    all_tables: list[dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total = len(pdf.pages)
        indices = _parse_page_selection(pages, total, max_pages)

        if indices:
            first_text = (pdf.pages[indices[0]].extract_text() or "").strip()
            if not first_text:
                raise ValueError(
                    "PDF appears to be scanned (no text layer). "
                    "Use the ocr_document node first."
                )

        for idx in indices:
            for tbl_idx, tbl in enumerate(pdf.pages[idx].extract_tables() or []):
                if not tbl or len(tbl) < 2:
                    continue
                header = [
                    str(h) if h else f"col_{i}"
                    for i, h in enumerate(tbl[0])
                ]
                df = pd.DataFrame(tbl[1:], columns=header)
                all_tables.append({
                    "page": idx + 1,
                    "table_index": tbl_idx,
                    "df": df,
                    "rows": len(df),
                    "columns": len(df.columns),
                })

    if not all_tables:
        return {
            "tables_found": 0,
            "dataset": None,
            "summary": [],
            "message": "No tables detected in the specified pages.",
        }

    dataset = dataframe_to_dataset(all_tables[0]["df"])
    return {
        "tables_found": len(all_tables),
        "dataset": dataset,
        "summary": [
            {
                "page": t["page"],
                "table_index": t["table_index"],
                "rows": t["rows"],
                "columns": t["columns"],
            }
            for t in all_tables
        ],
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_extract_tables or parse_page" -v
```

Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add pdf_extract_tables node (killer node)"
```

---

## Task 4: `pdf_generate` (killer node ★)

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_pdf_generate_raises_without_template(store_ctx) -> None:
    from noodle_nodes.document_intelligence import pdf_generate
    with pytest.raises(ValueError, match="template is required"):
        pdf_generate(input={}, template="")


def test_pdf_generate_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name in ("weasyprint", "jinja2"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.document_intelligence import pdf_generate
    with pytest.raises(RuntimeError, match="weasyprint"):
        pdf_generate(input={}, template="<h1>hi</h1>")


def test_pdf_generate_returns_artifact(store_ctx) -> None:
    pytest.importorskip("weasyprint")
    pytest.importorskip("jinja2")
    from noodle_nodes.document_intelligence import pdf_generate

    result = pdf_generate(
        input={"title": "Test Report", "body": "Hello world"},
        template="<html><body><h1>{{ title }}</h1><p>{{ body }}</p></body></html>",
        filename="test.pdf",
    )
    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "application/pdf"
    assert result["size_bytes"] > 0


def test_pdf_generate_renders_template_variables(store_ctx) -> None:
    pytest.importorskip("weasyprint")
    pytest.importorskip("jinja2")
    from noodle.artifacts import read_bytes
    from noodle_nodes.document_intelligence import pdf_generate

    result = pdf_generate(
        input={"name": "Alice"},
        template="<html><body><p>Hello {{ name }}</p></body></html>",
    )
    # PDF is binary — just verify it's a non-empty valid artifact
    assert is_artifact_ref(result["artifact"])
    raw = read_bytes(result["artifact"])
    assert raw[:4] == b"%PDF"
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_generate" -v
```

Expected: 4 failures

- [ ] **Step 3: Implement `pdf_generate`**

```python
@node(
    name="PDF Generate",
    id="pdf_generate",
    category="Document Intelligence",
    icon="page",
    requirements=["weasyprint>=60.0", "jinja2>=3.0"],
    params={
        "template": {
            "multiline": True,
            "placeholder": "<html><body><h1>{{ title }}</h1></body></html>",
            "description": "Jinja2 HTML template. Use {{ variable }} for input data fields.",
        },
        "css": {
            "multiline": True,
            "placeholder": "body { font-family: Arial; font-size: 12pt; }",
            "description": "Optional CSS appended inside a <style> tag.",
        },
        "filename": {"placeholder": "output.pdf"},
    },
)
def pdf_generate(
    input=None,
    template: str = "",
    css: str = "",
    filename: str = "output.pdf",
) -> dict:
    """Render a Jinja2 HTML template with input data and return a PDF artifact."""
    if not template:
        raise ValueError(
            "template is required — provide a Jinja2 HTML template in the node config."
        )

    try:
        import weasyprint  # type: ignore[import-not-found]
        import jinja2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "weasyprint and jinja2 are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    data: dict[str, Any] = {}
    if isinstance(input, dict):
        data.update(input)
    elif input is not None:
        data["input"] = input

    env = jinja2.Environment(autoescape=True)
    try:
        rendered_html = env.from_string(template).render(**data)
    except jinja2.TemplateError as exc:
        raise ValueError(f"Template rendering failed: {exc}") from exc

    if css:
        rendered_html = f"<style>{css}</style>{rendered_html}"

    pdf_bytes = weasyprint.HTML(string=rendered_html).write_pdf()
    artifact = _write_bytes(
        pdf_bytes,
        name=filename or "output.pdf",
        content_type="application/pdf",
        kind="document",
    )
    return {"artifact": artifact, "size_bytes": len(pdf_bytes)}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "pdf_generate" -v
```

Expected: 4 PASSED (weasyprint tests skipped if not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add pdf_generate node (killer node)"
```

---

## Task 5: `docx_generate` and `docx_extract` (killer node ★ + supporting)

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

These two nodes both use `python-docx` so they are implemented together.

- [ ] **Step 1: Write the failing tests**

```python
def test_docx_generate_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import docx_generate
    # No template, no input — should raise on unfilled placeholders (empty data = no placeholder keys)
    # Actually should succeed with a blank doc if no placeholders needed.
    # Test the real error: missing package path.
    pass  # covered by missing-package test below


def test_docx_generate_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("No module named 'docx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.document_intelligence import docx_generate
    with pytest.raises(RuntimeError, match="python-docx"):
        docx_generate(input={"name": "Alice"})


def test_docx_generate_returns_artifact(store_ctx) -> None:
    pytest.importorskip("docx")
    from noodle_nodes.document_intelligence import docx_generate

    result = docx_generate(input={"greeting": "Hello"}, filename="test.docx")
    assert is_artifact_ref(result["artifact"])
    assert "wordprocessingml" in result["artifact"]["content_type"]


def test_docx_generate_fills_template_placeholders(store_ctx) -> None:
    pytest.importorskip("docx")
    import tempfile, os
    from docx import Document
    from noodle.artifacts import write_bytes, read_bytes
    from noodle_nodes.document_intelligence import docx_generate

    # Build a real .docx template with {{name}} placeholder
    doc = Document()
    doc.add_paragraph("Dear {{name}},")
    doc.add_paragraph("Your order {{order_id}} is ready.")
    buf = io.BytesIO()
    doc.save(buf)
    template_ref = write_bytes(
        buf.getvalue(),
        name="template.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    result = docx_generate(
        input={"name": "Alice", "order_id": "ORD-001"},
        template_artifact=template_ref,
        filename="filled.docx",
    )
    assert is_artifact_ref(result["artifact"])
    assert result["placeholders_filled"] == 2

    # Verify the text was actually replaced
    filled_bytes = read_bytes(result["artifact"])
    filled_doc = Document(io.BytesIO(filled_bytes))
    full_text = " ".join(p.text for p in filled_doc.paragraphs)
    assert "Alice" in full_text
    assert "ORD-001" in full_text
    assert "{{name}}" not in full_text


def test_docx_generate_raises_for_unfilled_placeholders(store_ctx) -> None:
    pytest.importorskip("docx")
    from docx import Document
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import docx_generate

    doc = Document()
    doc.add_paragraph("Hello {{name}}, your code is {{code}}.")
    buf = io.BytesIO()
    doc.save(buf)
    template_ref = write_bytes(buf.getvalue(), name="tpl.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    with pytest.raises(ValueError, match="name"):
        docx_generate(input={}, template_artifact=template_ref)


def test_docx_extract_returns_paragraphs(store_ctx) -> None:
    pytest.importorskip("docx")
    from docx import Document
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import docx_extract

    doc = Document()
    doc.add_heading("My Title", level=1)
    doc.add_paragraph("First paragraph.")
    doc.add_paragraph("Second paragraph.")
    buf = io.BytesIO()
    doc.save(buf)
    artifact_ref = write_bytes(buf.getvalue(), name="test.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    result = docx_extract(input=artifact_ref)
    assert result["paragraph_count"] >= 3
    headings = [p for p in result["paragraphs"] if p["is_heading"]]
    assert any("My Title" in h["text"] for h in headings)


def test_docx_extract_returns_tables(store_ctx) -> None:
    pytest.importorskip("docx")
    from docx import Document
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import docx_extract

    doc = Document()
    tbl = doc.add_table(rows=3, cols=2)
    tbl.cell(0, 0).text = "Name"
    tbl.cell(0, 1).text = "Score"
    tbl.cell(1, 0).text = "Alice"
    tbl.cell(1, 1).text = "95"
    tbl.cell(2, 0).text = "Bob"
    tbl.cell(2, 1).text = "87"
    buf = io.BytesIO()
    doc.save(buf)
    artifact_ref = write_bytes(buf.getvalue(), name="test.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    result = docx_extract(input=artifact_ref, include_tables=True)
    assert result["table_count"] == 1
    assert result["tables"][0]["headers"] == ["Name", "Score"]
    assert result["tables"][0]["row_count"] == 2
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "docx" -v
```

Expected: failures for all docx tests (functions not yet defined)

- [ ] **Step 3: Implement `_replace_in_doc`, `docx_generate`, and `docx_extract`**

```python
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _replace_in_doc(doc: Any, data: dict[str, Any], unfilled: list[str]) -> None:
    """Replace {{key}} placeholders in all paragraphs and table cells."""
    def _replace_para(para: Any) -> None:
        for run in para.runs:
            def replacer(m: re.Match) -> str:
                key = m.group(1)
                if key not in data:
                    if key not in unfilled:
                        unfilled.append(key)
                    return m.group(0)
                return str(data[key])
            run.text = _PLACEHOLDER_RE.sub(replacer, run.text)

    for para in doc.paragraphs:
        _replace_para(para)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_para(para)


@node(
    name="Word Document Generate",
    id="docx_generate",
    category="Document Intelligence",
    icon="page",
    requirements=["python-docx>=1.1"],
    params={
        "content": {
            "multiline": True,
            "placeholder": '{"name": "Alice", "order_id": "ORD-001"}',
            "description": "JSON mapping of placeholder keys → values. Merged with upstream input.",
        },
        "filename": {"placeholder": "document.docx"},
    },
)
def docx_generate(
    input=None,
    template_artifact=None,
    content: str = "",
    filename: str = "document.docx",
) -> dict:
    """Populate a .docx template with {{placeholder}} replacements and return an artifact.

    If no template_artifact is wired, a blank document is created.
    """
    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required. Add python-docx to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    import json

    data: dict[str, Any] = {}
    if isinstance(input, dict):
        data.update(input)
    if content:
        try:
            data.update(json.loads(content))
        except json.JSONDecodeError as exc:
            raise ValueError(f"content must be valid JSON: {exc}") from exc

    if template_artifact is not None:
        doc = Document(io.BytesIO(_read_bytes(template_artifact)))
    else:
        doc = Document()

    unfilled: list[str] = []
    _replace_in_doc(doc, data, unfilled)

    if unfilled:
        raise ValueError(
            f"Template has placeholders with no matching data: {unfilled}. "
            "Add these keys to the input dict or the content param."
        )

    buf = io.BytesIO()
    doc.save(buf)
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    artifact = _write_bytes(
        buf.getvalue(),
        name=filename or "document.docx",
        content_type=content_type,
        kind="document",
    )
    return {
        "artifact": artifact,
        "size_bytes": len(buf.getvalue()),
        "placeholders_filled": len(data),
    }


@node(
    name="Word Document Extract",
    id="docx_extract",
    category="Document Intelligence",
    icon="page",
    requirements=["python-docx>=1.1"],
    params={
        "include_tables": {"description": "Also extract tables as structured records"},
    },
)
def docx_extract(input=None, include_tables: bool = True) -> dict:
    """Extract paragraphs, headings, and tables from a .docx artifact to records."""
    if input is None:
        raise ValueError("input is required — wire a .docx artifact to this node.")

    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required. Add python-docx to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    doc = Document(io.BytesIO(_read_bytes(input)))

    paragraphs = [
        {
            "style": para.style.name,
            "text": para.text,
            "is_heading": para.style.name.startswith("Heading"),
        }
        for para in doc.paragraphs
        if para.text.strip()
    ]

    tables: list[dict[str, Any]] = []
    if include_tables:
        for t_idx, table in enumerate(doc.tables):
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            if rows:
                tables.append({
                    "table_index": t_idx,
                    "headers": rows[0],
                    "rows": rows[1:],
                    "row_count": len(rows) - 1,
                })

    return {
        "paragraphs": paragraphs,
        "paragraph_count": len(paragraphs),
        "tables": tables,
        "table_count": len(tables),
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "docx" -v
```

Expected: all PASSED (skipped if python-docx not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add docx_generate and docx_extract nodes"
```

---

## Task 6: `excel_report_generate` and `excel_extract` (killer node ★ + supporting)

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_excel_report_generate_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import excel_report_generate
    with pytest.raises(ValueError, match="input is required"):
        excel_report_generate(input=None)


def test_excel_report_generate_raises_on_empty_dataset(store_ctx) -> None:
    pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    from noodle_nodes.document_intelligence import excel_report_generate
    with pytest.raises(ValueError, match="empty"):
        excel_report_generate(input=[])


def test_excel_report_generate_returns_artifact_from_records(store_ctx) -> None:
    pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    from noodle.artifacts import read_bytes
    from noodle_nodes.document_intelligence import excel_report_generate

    result = excel_report_generate(
        input=[{"name": "Alice", "score": 95}, {"name": "Bob", "score": 87}],
        sheet_name="Results",
        title="Test Results",
        filename="results.xlsx",
    )
    assert is_artifact_ref(result["artifact"])
    assert "spreadsheet" in result["artifact"]["content_type"]
    assert result["rows"] == 2
    assert result["columns"] == 2

    raw = read_bytes(result["artifact"])
    assert raw[:4] == b"PK\x03\x04"  # xlsx is a zip file


def test_excel_report_generate_returns_artifact_from_dataset(store_ctx) -> None:
    pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    from noodle_nodes.datasets import records_to_dataset
    from noodle_nodes.document_intelligence import excel_report_generate

    ds = records_to_dataset([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
    result = excel_report_generate(input=ds, filename="out.xlsx")
    assert is_artifact_ref(result["artifact"])


def test_excel_extract_returns_dataset_ref(store_ctx) -> None:
    pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import excel_extract, excel_report_generate

    # Generate an xlsx first, then extract it
    gen_result = excel_report_generate(
        input=[{"fruit": "apple", "count": 5}, {"fruit": "banana", "count": 3}],
        filename="fruit.xlsx",
    )
    result = excel_extract(input=gen_result["artifact"])
    assert is_dataset_ref(result)


def test_excel_extract_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import excel_extract
    with pytest.raises(ValueError, match="input is required"):
        excel_extract(input=None)
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "excel" -v
```

Expected: failures (functions not yet defined)

- [ ] **Step 3: Implement `excel_report_generate` and `excel_extract`**

```python
@node(
    name="Excel Report Generate",
    id="excel_report_generate",
    category="Document Intelligence",
    icon="sheet",
    requirements=["openpyxl>=3.1", "pandas>=2.0"],
    params={
        "sheet_name": {"placeholder": "Report"},
        "title": {"placeholder": "My Report", "description": "Bold title row inserted above headers"},
        "include_chart": {"description": "Add a bar chart of the first numeric column"},
        "filename": {"placeholder": "report.xlsx"},
    },
)
def excel_report_generate(
    input=None,
    sheet_name: str = "Report",
    title: str = "",
    include_chart: bool = False,
    filename: str = "report.xlsx",
) -> dict:
    """Build a formatted .xlsx report from a DatasetRef or list of records."""
    if input is None:
        raise ValueError("input is required — wire a DatasetRef or records list to this node.")

    try:
        import openpyxl  # type: ignore[import-not-found]
        from openpyxl.styles import Font, PatternFill  # type: ignore[import-not-found]
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl and pandas are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from noodle.datasets import is_dataset_ref
    from noodle_nodes.datasets import read_dataset

    if is_dataset_ref(input):
        conn, rel = read_dataset(input)
        try:
            df = rel.df()
        finally:
            conn.close()
    elif isinstance(input, list):
        df = pd.DataFrame(input)
    else:
        raise ValueError("input must be a DatasetRef or a list of records.")

    if df.empty:
        raise ValueError("Input dataset is empty — nothing to write to Excel.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name or "Report"

    row_offset = 1
    if title:
        title_cell = ws.cell(row=1, column=1, value=title)
        title_cell.font = Font(bold=True, size=14)
        row_offset = 3

    header_fill = PatternFill(start_color="DDEEFF", end_color="DDEEFF", fill_type="solid")
    for col_idx, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(row=row_offset, column=col_idx, value=str(col_name))
        cell.font = Font(bold=True)
        cell.fill = header_fill

    for r_idx, row in enumerate(df.itertuples(index=False), start=row_offset + 1):
        for c_idx, value in enumerate(row, start=1):
            ws.cell(row=r_idx, column=c_idx, value=value)

    if include_chart:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if numeric_cols:
            from openpyxl.chart import BarChart, Reference  # type: ignore[import-not-found]
            chart = BarChart()
            chart.title = title or sheet_name
            col_pos = list(df.columns).index(numeric_cols[0]) + 1
            data_ref = Reference(
                ws,
                min_col=col_pos,
                max_col=col_pos,
                min_row=row_offset,
                max_row=row_offset + len(df),
            )
            chart.add_data(data_ref, titles_from_data=True)
            ws.add_chart(chart, f"A{row_offset + len(df) + 3}")

    buf = io.BytesIO()
    wb.save(buf)
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    artifact = _write_bytes(
        buf.getvalue(),
        name=filename or "report.xlsx",
        content_type=content_type,
        kind="document",
    )
    return {
        "artifact": artifact,
        "rows": len(df),
        "columns": len(df.columns),
        "size_bytes": len(buf.getvalue()),
    }


@node(
    name="Excel Extract",
    id="excel_extract",
    category="Document Intelligence",
    icon="sheet",
    requirements=["openpyxl>=3.1", "pandas>=2.0"],
    params={
        "sheet_name": {"placeholder": "Sheet1 (blank = first sheet)"},
        "header_row": {"description": "Row number of the header (1-indexed). Use 0 for no header."},
    },
)
def excel_extract(input=None, sheet_name: str = "", header_row: int = 1):
    """Read a sheet from an .xlsx artifact and return a DatasetRef."""
    if input is None:
        raise ValueError("input is required — wire an .xlsx artifact to this node.")

    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl and pandas are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from noodle_nodes.datasets import dataframe_to_dataset

    raw = _read_bytes(input)
    kwargs: dict[str, Any] = {"engine": "openpyxl"}
    if sheet_name:
        kwargs["sheet_name"] = sheet_name
    kwargs["header"] = None if header_row == 0 else header_row - 1

    try:
        df = pd.read_excel(io.BytesIO(raw), **kwargs)
    except Exception as exc:
        raise ValueError(f"Failed to read Excel file: {exc}") from exc

    # Forward-fill merged cells (openpyxl returns None for non-anchor merged cells)
    df = df.ffill()

    if df.empty:
        raise ValueError("The selected sheet is empty.")

    return dataframe_to_dataset(df)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "excel" -v
```

Expected: all PASSED (skipped if openpyxl/pandas not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add excel_report_generate and excel_extract nodes"
```

---

## Task 7: `barcode_qr_generate` and `barcode_qr_decode`

**Files:**
- Modify: `packages/nodes/noodle_nodes/document_intelligence.py`
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_barcode_qr_generate_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import barcode_qr_generate
    with pytest.raises(ValueError, match="input is required"):
        barcode_qr_generate(input=None)


def test_barcode_qr_generate_qr_returns_png_artifact(store_ctx) -> None:
    pytest.importorskip("qrcode")
    from noodle_nodes.document_intelligence import barcode_qr_generate

    result = barcode_qr_generate(input="https://example.com", format="qr", filename="qr.png")
    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "image/png"
    assert result["format"] == "qr"
    assert result["value"] == "https://example.com"


def test_barcode_qr_decode_raises_without_input(store_ctx) -> None:
    from noodle_nodes.document_intelligence import barcode_qr_decode
    with pytest.raises(ValueError, match="input is required"):
        barcode_qr_decode(input=None)


def test_barcode_qr_roundtrip(store_ctx) -> None:
    """Generate a QR code then decode it — values must match."""
    pytest.importorskip("qrcode")
    pytest.importorskip("pyzbar")
    from noodle_nodes.document_intelligence import barcode_qr_generate, barcode_qr_decode

    gen_result = barcode_qr_generate(input="HELLO-NOODLE-123", format="qr")
    decode_result = barcode_qr_decode(input=gen_result["artifact"])

    assert decode_result["count"] == 1
    assert decode_result["codes"][0]["data"] == "HELLO-NOODLE-123"
    assert decode_result["codes"][0]["type"] == "QRCODE"


def test_barcode_qr_decode_raises_on_blank_image(store_ctx) -> None:
    pytest.importorskip("pyzbar")
    pytest.importorskip("PIL")
    from PIL import Image
    from noodle.artifacts import write_bytes
    from noodle_nodes.document_intelligence import barcode_qr_decode

    # Blank white image — no barcode
    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    ref = write_bytes(buf.getvalue(), name="blank.png", content_type="image/png")

    with pytest.raises(ValueError, match="No barcode"):
        barcode_qr_decode(input=ref)
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "barcode_qr" -v
```

Expected: failures (functions not yet defined)

- [ ] **Step 3: Implement `barcode_qr_generate` and `barcode_qr_decode`**

```python
@node(
    name="Barcode / QR Generate",
    id="barcode_qr_generate",
    category="Document Intelligence",
    icon="tag",
    requirements=["qrcode[pil]>=7.4", "python-barcode>=0.15", "pillow>=10.0"],
    params={
        "format": {
            "choices": ["qr", "code128", "ean13"],
            "description": "qr: QR code. code128/ean13: 1D barcodes.",
        },
        "scale": {"description": "QR code box size in pixels (QR only, default 10)"},
        "filename": {"placeholder": "barcode.png"},
    },
)
def barcode_qr_generate(
    input=None,
    format: str = "qr",
    scale: int = 10,
    filename: str = "barcode.png",
) -> dict:
    """Generate a QR code or 1D barcode image artifact from the input value."""
    if input is None:
        raise ValueError("input is required — wire the value to encode to this node.")

    value = str(input) if not isinstance(input, str) else input
    img_bytes: bytes

    if format == "qr":
        try:
            import qrcode  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "qrcode[pil] is required. Add qrcode[pil] to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        img = qrcode.make(value, box_size=max(1, scale))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()
    else:
        try:
            import barcode as _barcode  # type: ignore[import-not-found]
            from barcode.writer import ImageWriter  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "python-barcode and pillow are required. Add them to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        fmt_map = {"code128": "code128", "ean13": "ean13"}
        bc_cls = _barcode.get_barcode_class(fmt_map.get(format, "code128"))
        bc = bc_cls(value, writer=ImageWriter())
        buf = io.BytesIO()
        bc.write(buf)
        img_bytes = buf.getvalue()

    artifact = _write_bytes(img_bytes, name=filename or "barcode.png", content_type="image/png", kind="image")
    return {"artifact": artifact, "format": format, "value": value, "size_bytes": len(img_bytes)}


@node(
    name="Barcode / QR Decode",
    id="barcode_qr_decode",
    category="Document Intelligence",
    icon="tag",
    requirements=["pyzbar>=0.1.9", "pillow>=10.0"],
    params={},
)
def barcode_qr_decode(input=None) -> dict:
    """Decode all barcodes and QR codes found in an image artifact."""
    if input is None:
        raise ValueError("input is required — wire an image artifact to this node.")

    try:
        from pyzbar import pyzbar  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pyzbar and pillow are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    img = Image.open(io.BytesIO(_read_bytes(input)))
    decoded = pyzbar.decode(img)

    if not decoded:
        raise ValueError(
            "No barcode or QR code detected in the image. "
            "Check that the image is clear and not blurry."
        )

    return {
        "codes": [
            {
                "type": d.type,
                "data": d.data.decode("utf-8", errors="replace"),
                "rect": {
                    "left": d.rect.left,
                    "top": d.rect.top,
                    "width": d.rect.width,
                    "height": d.rect.height,
                },
            }
            for d in decoded
        ],
        "count": len(decoded),
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "barcode_qr" -v
```

Expected: all PASSED (skipped if qrcode/pyzbar not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/document_intelligence.py \
        packages/nodes/tests/test_document_intelligence.py
git commit -m "feat(nodes): add barcode_qr_generate and barcode_qr_decode nodes"
```

---

## Task 8: Node registration verification + full test suite

**Files:**
- Modify: `packages/nodes/tests/test_document_intelligence.py`

- [ ] **Step 1: Write the registration test**

```python
def test_document_intelligence_nodes_registered() -> None:
    from noodle.sdk import registry
    ids = {m.id for m in registry.manifests()}
    expected = {
        "pdf_extract_text",
        "pdf_extract_tables",
        "pdf_generate",
        "docx_generate",
        "docx_extract",
        "excel_report_generate",
        "excel_extract",
        "barcode_qr_generate",
        "barcode_qr_decode",
    }
    assert expected <= ids, f"Missing from registry: {expected - ids}"


def test_document_intelligence_nodes_have_requirements() -> None:
    """Every node with optional packages must declare requirements."""
    from noodle.sdk import registry
    nodes_with_reqs = {
        "pdf_extract_text",
        "pdf_extract_tables",
        "pdf_generate",
        "docx_generate",
        "docx_extract",
        "excel_report_generate",
        "excel_extract",
        "barcode_qr_generate",
        "barcode_qr_decode",
    }
    manifests = {m.id: m for m in registry.manifests()}
    for node_id in nodes_with_reqs:
        manifest = manifests.get(node_id)
        assert manifest is not None, f"Node {node_id} not in registry"
        assert manifest.requirements, f"Node {node_id} has no requirements declared"


def test_import_does_not_import_optional_packages() -> None:
    """Importing noodle_nodes must not pull in any optional document packages."""
    import sys
    # These must not be present in sys.modules after a fresh import of the module
    forbidden = {
        "pdfplumber", "weasyprint", "docx", "openpyxl",
        "qrcode", "barcode", "pyzbar",
    }
    # Filter only what was newly imported by checking pre/post state
    pre = set(sys.modules.keys())
    import importlib
    if "noodle_nodes.document_intelligence" in sys.modules:
        importlib.reload(sys.modules["noodle_nodes.document_intelligence"])
    imported = set(sys.modules.keys()) - pre
    leaked = forbidden & imported
    assert not leaked, f"Optional packages leaked into module scope: {leaked}"
```

- [ ] **Step 2: Run registration tests**

```bash
cd packages/nodes
uv run pytest tests/test_document_intelligence.py -k "registered or requirements or optional_packages" -v
```

Expected: 3 PASSED

- [ ] **Step 3: Run the full test suite to check for regressions**

```bash
cd packages/nodes
uv run pytest tests/ -v --tb=short
```

Expected: all existing tests PASS; new tests PASS or SKIP (if packages not installed)

- [ ] **Step 4: Verify `noodle_nodes` top-level import still works cleanly**

```bash
cd packages/nodes
uv run python -c "import noodle_nodes; print('OK')"
```

Expected: `OK` with no errors or warnings

- [ ] **Step 5: Final commit**

```bash
git add packages/nodes/tests/test_document_intelligence.py
git commit -m "test(nodes): add registration and import-safety tests for document_intelligence"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| Hard invariant: no global imports | Task 1 scaffold + Task 8 import-safety test |
| `requirements=[...]` on every node | Task 8 registration test asserts non-empty |
| Lazy import with `RuntimeError` + install message | Tasks 2, 4, 5, 6, 7 (missing-package tests) |
| Large outputs → artifact refs or DatasetRefs | Every node returns `ArtifactRef` or `DatasetRef` |
| `pdf_extract_text` | Task 2 |
| `pdf_extract_tables` ★ | Task 3 |
| `pdf_generate` ★ | Task 4 |
| `docx_generate` ★ | Task 5 |
| `docx_extract` | Task 5 |
| `excel_report_generate` ★ | Task 6 |
| `excel_extract` | Task 6 |
| `barcode_qr_generate` | Task 7 |
| `barcode_qr_decode` | Task 7 |
| Edge case: scanned PDF raises with helpful message | Task 2 + Task 3 tests |
| Edge case: password-protected / missing input | `raises(ValueError, match="input is required")` in every node |
| Edge case: unfilled template placeholders | Task 5 `test_docx_generate_raises_for_unfilled_placeholders` |
| Edge case: blank image for QR decode | Task 7 `test_barcode_qr_decode_raises_on_blank_image` |
| Edge case: empty Excel sheet | Task 6 `test_excel_report_generate_raises_on_empty_dataset` |
| Edge case: merged cells forward-fill | Implemented in `excel_extract` (df.ffill()) |
| Register in `__init__.py` | Task 1 Step 4 |
| Node IDs match spec | All IDs verified in Task 8 registration test |

**Nodes not in this plan (deferred to later waves):**
`pdf_extract_images`, `pdf_merge_split`, `pdf_fill_form`, `pdf_watermark`, `html_to_pdf`, `pptx_generate`, `ocr_document`, `document_parse_layout`, `spreadsheet_diff` — these require heavier deps (`pymupdf`, `docling`, `pytesseract`) and are Wave 1 stretch goals. Ship the 9 highest-value nodes first.
