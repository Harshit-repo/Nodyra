"""PDF processing nodes."""

from __future__ import annotations

import os
from typing import Any

from noodle.sdk import node


def _resolve_path(path: str) -> str:
    resolved = os.path.realpath(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"PDF file not found: {path}")
    return resolved


def _pymupdf():
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PDF processing requires the `PyMuPDF` (fitz) package. "
            "Install with: uv pip install PyMuPDF"
        ) from exc
    return fitz


@node(
    name="PDF Extract Text",
    id="pdf_extract_text_v2",
    category="Files",
    icon="file-text",
    params={
        "path": {
            "description": "Path to the PDF file.",
            "placeholder": "/path/to/document.pdf",
        },
        "page_start": {
            "group": "Options",
            "description": "First page (0-indexed).",
        },
        "page_end": {
            "group": "Options",
            "description": "Last page (0=all remaining).",
        },
    },
)
def pdf_extract_text(
    input: Any = None,
    path: str = "",
    page_start: int = 0,
    page_end: int = 0,
) -> dict[str, Any]:
    """Extract text from a PDF file."""
    if not path:
        raise ValueError("pdf_extract_text: path is required")

    resolved = _resolve_path(path)

    fitz = _pymupdf()
    doc = fitz.open(resolved)

    try:
        total_pages = doc.page_count
        if total_pages == 0:
            return {"text": "", "page_count": 0, "pages_extracted": 0}

        start = max(0, int(page_start))
        end = int(page_end)
        if end <= 0 or end > total_pages:
            end = total_pages

        start = min(start, total_pages - 1)

        pages = []
        for page_num in range(start, end):
            page = doc.load_page(page_num)
            pages.append(page.get_text())

        text = "\n\n".join(pages)
        return {
            "text": text,
            "page_count": total_pages,
            "pages_extracted": end - start,
        }
    finally:
        doc.close()


@node(
    name="PDF Merge",
    id="pdf_merge",
    category="Files",
    icon="file-text",
    params={
        "paths": {
            "multiline": True,
            "description": "PDF file paths to merge, one per line.",
            "placeholder": "/path/to/file1.pdf\n/path/to/file2.pdf",
        },
        "output_path": {
            "description": "Output file path.",
            "placeholder": "/path/to/merged.pdf",
        },
    },
)
def pdf_merge(
    input: Any = None,
    paths: str = "",
    output_path: str = "",
) -> dict[str, Any]:
    """Merge multiple PDF files into one."""
    if not paths:
        raise ValueError("pdf_merge: paths is required")

    if not output_path:
        raise ValueError("pdf_merge: output_path is required")

    file_list = [p.strip() for p in paths.strip().split("\n") if p.strip()]
    if not file_list:
        raise ValueError("pdf_merge: at least one PDF path must be provided")

    for fp in file_list:
        _resolve_path(fp)

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    fitz = _pymupdf()
    merged = fitz.open()
    total_pages = 0

    try:
        for fp in file_list:
            src = fitz.open(fp)
            try:
                total_pages += src.page_count
                merged.insert_pdf(src)
            finally:
                src.close()

        merged.save(output_path)
    finally:
        merged.close()

    return {
        "output_path": os.path.abspath(output_path),
        "page_count": total_pages,
        "source_count": len(file_list),
    }


@node(
    name="PDF Split",
    id="pdf_split",
    category="Files",
    icon="file-text",
    params={
        "path": {
            "description": "Path to the PDF file to split.",
            "placeholder": "/path/to/document.pdf",
        },
        "output_dir": {
            "description": "Directory for output files.",
            "placeholder": "/path/to/output/",
        },
        "pages_per_file": {
            "group": "Options",
            "description": "Pages per output file (0=each page separate).",
        },
        "naming": {
            "group": "Options",
            "placeholder": "{name}_page_{page:04d}.pdf",
            "description": "Output filename pattern. {name}=original filename, {page}=page number.",
        },
    },
)
def pdf_split(
    input: Any = None,
    path: str = "",
    output_dir: str = "",
    pages_per_file: int = 1,
    naming: str = "{name}_page_{page:04d}.pdf",
) -> dict[str, Any]:
    """Split a PDF file into multiple files."""
    if not path:
        raise ValueError("pdf_split: path is required")

    if not output_dir:
        raise ValueError("pdf_split: output_dir is required")

    resolved = _resolve_path(path)

    if not os.path.isdir(output_dir):
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    fitz = _pymupdf()
    doc = fitz.open(resolved)

    try:
        total = doc.page_count
        if total == 0:
            return {"files": [], "count": 0}

        chunk_size = max(1, int(pages_per_file))
        base_name = os.path.splitext(os.path.basename(path))[0]
        output_files = []
        page_num = 0

        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            out_doc = fitz.open()
            try:
                out_doc.insert_pdf(doc, from_page=start, to_page=end - 1)
                filename = naming.replace("{name}", base_name).replace("{page}", f"{page_num + 1:04d}")
                out_path = os.path.join(output_dir, filename)
                out_doc.save(out_path)
                output_files.append(os.path.abspath(out_path))
            finally:
                out_doc.close()
            page_num += 1

        return {"files": output_files, "count": len(output_files)}
    finally:
        doc.close()


@node(
    name="PDF Get Info",
    id="pdf_get_info",
    category="Files",
    icon="file-text",
    tool_side_effecting=False,
    params={
        "path": {
            "description": "Path to the PDF file.",
            "placeholder": "/path/to/document.pdf",
        },
    },
)
def pdf_get_info(
    input: Any = None,
    path: str = "",
) -> dict[str, Any]:
    """Get metadata and info from a PDF file."""
    if not path:
        raise ValueError("pdf_get_info: path is required")

    resolved = _resolve_path(path)

    fitz = _pymupdf()
    doc = fitz.open(resolved)

    try:
        meta = doc.metadata
        encryption = None
        if doc.is_encrypted:
            encryption = "encrypted"

        return {
            "page_count": doc.page_count,
            "title": meta.get("title", "") or "",
            "author": meta.get("author", "") or "",
            "subject": meta.get("subject", "") or "",
            "keywords": meta.get("keywords", "") or "",
            "producer": meta.get("producer", "") or "",
            "creator": meta.get("creator", "") or "",
            "format": meta.get("format", "") or "",
            "encryption": encryption,
            "file_size_bytes": os.path.getsize(path),
        }
    finally:
        doc.close()


@node(
    name="PDF to Images",
    id="pdf_to_images",
    category="Files",
    icon="file-text",
    params={
        "path": {
            "description": "Path to the PDF file.",
            "placeholder": "/path/to/document.pdf",
        },
        "output_dir": {
            "description": "Directory for output images.",
            "placeholder": "/path/to/output/",
        },
        "dpi": {
            "group": "Options",
            "type": "integer",
            "default": 200,
            "description": "Output image DPI.",
        },
        "format": {
            "group": "Options",
            "choices": ["png", "jpg", "webp"],
            "default": "png",
            "description": "Output image format.",
        },
        "page_start": {
            "group": "Options",
            "type": "integer",
            "default": 0,
            "description": "First page (0-indexed).",
        },
        "page_end": {
            "group": "Options",
            "type": "integer",
            "default": 0,
            "description": "Last page (0=all remaining).",
        },
    },
)
def pdf_to_images(
    input: Any = None,
    path: str = "",
    output_dir: str = "",
    dpi: int = 200,
    format: str = "png",
    page_start: int = 0,
    page_end: int = 0,
) -> dict[str, Any]:
    """Convert PDF pages to image files."""
    if not path:
        raise ValueError("pdf_to_images: path is required")
    if not output_dir:
        raise ValueError("pdf_to_images: output_dir is required")

    resolved = _resolve_path(path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    fitz = _pymupdf()
    doc = fitz.open(resolved)

    try:
        total = doc.page_count
        if total == 0:
            return {"files": [], "count": 0, "page_count": 0}

        start = max(0, int(page_start))
        end = int(page_end)
        if end <= 0 or end > total:
            end = total
        start = min(start, total - 1)

        base_name = Path(path).stem
        ext = format.replace("jpg", "jpeg")
        files = []
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        for page_num in range(start, end):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=mat)
            out_path = str(output / f"{base_name}_page_{page_num + 1:04d}.{ext}")
            pix.save(out_path)
            files.append(os.path.abspath(out_path))

        return {"files": files, "count": len(files), "page_count": total}
    finally:
        doc.close()


__all__ = [
    "pdf_extract_text",
    "pdf_merge",
    "pdf_split",
    "pdf_get_info",
    "pdf_to_images",
]
