"""Document intelligence nodes — PDF, Word, Excel, barcodes.

All heavy dependencies are lazy-imported inside each function body.
No optional package is imported at module scope.
"""

from __future__ import annotations

import io
import re
from typing import Any

from nodyra.artifacts import read_bytes as _read_bytes
from nodyra.artifacts import write_bytes as _write_bytes
from nodyra.artifacts import write_text as _write_text
from nodyra.sdk import node

# Nodes are defined below. Helpers first.


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


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _block_external_pdf_resource(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Reject external resources during HTML-to-PDF rendering."""
    _ = args, kwargs
    raise ValueError(f"pdf_generate: external resource fetch blocked: {url}")


def _replace_in_doc(doc: Any, data: dict[str, Any], unfilled: list[str]) -> None:
    """Replace {{key}} placeholders in all paragraphs and table cells."""
    def _replace_para(para: Any) -> None:
        for run in para.runs:
            def replacer(m: re.Match, _data: dict = data, _unfilled: list = unfilled) -> str:
                key = m.group(1)
                if key not in _data:
                    if key not in _unfilled:
                        _unfilled.append(key)
                    return m.group(0)
                return str(_data[key])
            run.text = _PLACEHOLDER_RE.sub(replacer, run.text)

    for para in doc.paragraphs:
        _replace_para(para)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_para(para)


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
        import pandas as pd  # type: ignore[import-not-found]
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber and pandas are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from nodyra_nodes.datasets import dataframe_to_dataset

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
        import jinja2  # type: ignore[import-not-found]
        import weasyprint  # type: ignore[import-not-found]
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

    pdf_bytes = weasyprint.HTML(
        string=rendered_html,
        url_fetcher=_block_external_pdf_resource,
    ).write_pdf()
    artifact = _write_bytes(
        pdf_bytes,
        name=filename or "output.pdf",
        content_type="application/pdf",
        kind="document",
    )
    return {"artifact": artifact, "size_bytes": len(pdf_bytes)}


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


@node(
    name="Excel Report Generate",
    id="excel_report_generate",
    category="Document Intelligence",
    icon="sheet",
    requirements=["openpyxl>=3.1", "pandas>=2.0"],
    params={
        "sheet_name": {"placeholder": "Report"},
        "title": {
            "placeholder": "My Report",
            "description": "Bold title row inserted above headers",
        },
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
        import pandas as pd  # type: ignore[import-not-found]
        from openpyxl.styles import Font, PatternFill  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl and pandas are required. Add them to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from nodyra.datasets import is_dataset_ref
    from nodyra_nodes.datasets import read_dataset

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

    from nodyra_nodes.datasets import dataframe_to_dataset

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

    artifact = _write_bytes(
        img_bytes,
        name=filename or "barcode.png",
        content_type="image/png",
        kind="image",
    )
    return {"artifact": artifact, "format": format, "value": value, "size_bytes": len(img_bytes)}


@node(
    name="Barcode / QR Decode",
    id="barcode_qr_decode",
    category="Document Intelligence",
    icon="tag",
    requirements=[
        "zxing-cpp>=2.2; sys_platform=='win32'",
        "pyzbar>=0.1.9; sys_platform!='win32'",
        "pillow>=10.0",
    ],
    params={},
)
def barcode_qr_decode(input=None) -> dict:
    """Decode all barcodes and QR codes found in an image artifact."""
    import sys as _sys
    if input is None:
        raise ValueError("input is required — wire an image artifact to this node.")

    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pillow is required. Add it to the workflow environment, rebuild it, then run again."
        ) from exc

    img = Image.open(io.BytesIO(_read_bytes(input)))

    if _sys.platform == "win32":
        try:
            import zxing_cpp as _zx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "zxing-cpp is required on Windows. Add 'zxing-cpp>=2.2' to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        raw = _zx.read_barcodes(img)
        codes = [
            {"type": r.format.name.upper(), "data": r.text, "rect": {}}
            for r in raw if r.text
        ]
    else:
        try:
            from pyzbar import pyzbar  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "pyzbar is required on Linux/macOS. Add 'pyzbar>=0.1.9' to the workflow "
                "environment, rebuild it, then run again."
            ) from exc
        codes = [
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
            for d in pyzbar.decode(img)
        ]

    if not codes:
        raise ValueError(
            "No barcode or QR code detected in the image. "
            "Check that the image is clear and not blurry."
        )

    return {"codes": codes, "count": len(codes)}
