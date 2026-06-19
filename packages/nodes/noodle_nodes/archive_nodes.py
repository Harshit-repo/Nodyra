"""Archive and compression nodes for file operations."""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from noodle.sdk import node


def _resolve_output(path: Path, output_dir: Path) -> Path:
    resolved = output_dir.resolve() / path
    resolved = resolved.resolve()
    if not str(resolved).startswith(str(output_dir.resolve())):
        raise ValueError(f"archive_extract: member '{path}' would extract outside target directory")
    return resolved


def _open_archive(path: str, mode: str = "r"):
    if path.endswith(".zip"):
        return zipfile.ZipFile(path, mode)
    if path.endswith((".tar.gz", ".tgz")):
        return tarfile.open(path, f"{mode}:gz")
    if path.endswith((".tar.bz2", ".tbz2")):
        return tarfile.open(path, f"{mode}:bz2")
    if path.endswith((".tar.xz", ".txz")):
        return tarfile.open(path, f"{mode}:xz")
    if path.endswith(".tar"):
        return tarfile.open(path, f"{mode}:")
    raise ValueError(f"Unknown archive format: {path}")


def _add_to_zip(zf: zipfile.ZipFile, source: str):
    p = Path(source)
    if p.is_dir():
        for file_path in sorted(p.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(p.parent)
                zf.write(str(file_path), str(rel))
    elif p.is_file():
        zf.write(str(p), p.name)


@node(
    name="Archive Create",
    id="archive_create",
    category="Files",
    icon="import",
    params={
        "source_paths": {
            "description": "Files/directories to archive, one per line.",
            "placeholder": "/path/to/file1.txt\n/path/to/dir1",
            "multiline": True,
        },
        "output_path": {
            "description": "Output archive path.",
            "placeholder": "/path/to/output.zip",
        },
        "format": {
            "description": "Archive format.",
            "choices": ["zip", "tar", "gztar", "bztar", "xztar"],
        },
        "compression_level": {
            "group": "Options",
            "type": "integer",
            "description": "Compression level 0-9.",
        },
    },
)
def archive_create(
    input: Any = None,
    source_paths: str = "",
    output_path: str = "",
    format: str = "zip",
    compression_level: int = 6,
) -> dict[str, Any]:
    """Create an archive from files and directories."""
    if not source_paths or not source_paths.strip():
        raise ValueError("archive_create: source_paths is required")

    paths = [s.strip() for s in source_paths.strip().split("\n") if s.strip()]
    if not paths:
        raise ValueError("archive_create: at least one source path is required")
    if not output_path:
        raise ValueError("archive_create: output_path is required")

    for p in paths:
        if not Path(p).exists():
            raise ValueError(f"archive_create: source path does not exist: {p}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fmt = format
    if fmt not in ("zip", "tar", "gztar", "bztar", "xztar"):
        raise ValueError(f"archive_create: unsupported format: {fmt}")

    level = max(0, min(9, int(compression_level or 6)))

    if fmt == "zip":
        with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED, compresslevel=level) as zf:
            for p in paths:
                _add_to_zip(zf, p)
    else:
        tar_mode = {"tar": "w", "gztar": "w:gz", "bztar": "w:bz2", "xztar": "w:xz"}[fmt]
        tar_kwargs: dict[str, Any] = {}
        if fmt in ("gztar", "bztar"):
            tar_kwargs["compresslevel"] = level
        elif fmt == "xztar":
            tar_kwargs["preset"] = level
        with tarfile.open(str(output), tar_mode, **tar_kwargs) as tf:
            for p in paths:
                tf.add(p)

    file_size = os.path.getsize(str(output))
    display_format = fmt.replace("gztar", "tar.gz").replace("bztar", "tar.bz2").replace("xztar", "tar.xz")
    return {
        "output_path": str(output.resolve()),
        "file_size_bytes": file_size,
        "format": display_format,
    }


@node(
    name="Archive Extract",
    id="archive_extract",
    category="Files",
    icon="import",
    params={
        "archive_path": {
            "description": "Path to the archive.",
            "placeholder": "/path/to/archive.zip",
        },
        "output_dir": {
            "description": "Directory to extract into.",
            "placeholder": "/path/to/output/",
        },
        "members": {
            "group": "Options",
            "multiline": True,
            "description": "Specific files to extract (one per line, blank=all).",
        },
    },
)
def archive_extract(
    input: Any = None,
    archive_path: str = "",
    output_dir: str = "",
    members: str | None = None,
) -> dict[str, Any]:
    """Extract an archive to a directory."""
    if not archive_path:
        raise ValueError("archive_extract: archive_path is required")
    if not Path(archive_path).exists():
        raise ValueError(f"archive_extract: archive not found: {archive_path}")
    if not output_dir:
        raise ValueError("archive_extract: output_dir is required")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    member_names: list[str] | None = None
    if members and members.strip():
        member_names = [m.strip() for m in members.strip().split("\n") if m.strip()]

    is_zip = archive_path.lower().endswith(".zip")

    if is_zip:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    raise ValueError("Encrypted ZIP archives are not supported")
                resolved = _resolve_output(Path(info.filename), output)
                if info.is_dir():
                    resolved.mkdir(parents=True, exist_ok=True)
                else:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(resolved, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            if member_names:
                all_names = zf.namelist()
                for m in member_names:
                    if m not in all_names:
                        raise ValueError(f"archive_extract: member not found in archive: {m}")
                extracted = member_names
            else:
                extracted = zf.namelist()
    else:
        with _open_archive(archive_path) as tf:
            for m in tf.getmembers():
                resolved = _resolve_output(Path(m.name), output)
                if m.isdir():
                    resolved.mkdir(parents=True, exist_ok=True)
                else:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(m) as src:
                        if src is None:
                            continue
                        with open(resolved, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            if member_names:
                for m in member_names:
                    try:
                        tf.getmember(m)
                    except KeyError:
                        raise ValueError(f"archive_extract: member not found in archive: {m}")
                extracted = member_names
            else:
                extracted = [m.name for m in tf.getmembers()]

    return {
        "extracted_files": extracted,
        "count": len(extracted),
        "output_dir": str(output.resolve()),
    }


@node(
    name="Archive List",
    id="archive_list",
    category="Files",
    icon="list",
    tool_side_effecting=False,
    params={
        "archive_path": {
            "description": "Path to the archive.",
            "placeholder": "/path/to/archive.zip",
        },
    },
)
def archive_list(
    input: Any = None,
    archive_path: str = "",
) -> dict[str, Any]:
    """List contents of an archive."""
    if not archive_path:
        raise ValueError("archive_list: archive_path is required")
    if not Path(archive_path).exists():
        raise ValueError(f"archive_list: archive not found: {archive_path}")

    files: list[dict[str, Any]] = []
    is_zip = archive_path.lower().endswith(".zip")

    if is_zip:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                files.append({
                    "name": info.filename,
                    "size_bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                    "is_dir": info.filename.endswith("/"),
                })
    else:
        with _open_archive(archive_path) as tf:
            for m in tf.getmembers():
                files.append({
                    "name": m.name,
                    "size_bytes": m.size,
                    "compressed_bytes": None,
                    "is_dir": m.isdir(),
                })

    files.sort(key=lambda x: x["name"])
    total_uncompressed = sum(f["size_bytes"] for f in files)
    return {
        "files": files,
        "count": len(files),
        "total_uncompressed": total_uncompressed,
    }


@node(
    name="File Compress",
    id="file_compress",
    category="Files",
    icon="import",
    params={
        "source_path": {
            "description": "File to compress.",
            "placeholder": "/path/to/file.txt",
        },
        "output_path": {
            "description": "Output compressed file path.",
            "placeholder": "/path/to/file.txt.gz",
        },
        "format": {
            "description": "Compression format.",
            "choices": ["gz", "bz2", "xz"],
        },
        "compression_level": {
            "group": "Options",
            "type": "integer",
            "description": "Level 0-9.",
        },
    },
)
def file_compress(
    input: Any = None,
    source_path: str = "",
    output_path: str = "",
    format: str = "gz",
    compression_level: int = 6,
) -> dict[str, Any]:
    """Compress a single file using gzip, bzip2, or xz."""
    if not source_path:
        raise ValueError("file_compress: source_path is required")
    source = Path(source_path)
    if not source.exists():
        raise ValueError(f"file_compress: file not found: {source_path}")
    if source.is_dir():
        raise ValueError("file_compress: source is a directory, not a file")
    if not output_path:
        raise ValueError("file_compress: output_path is required")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    level = max(0, min(9, int(compression_level or 6)))
    fmt = format

    source_size = source.stat().st_size

    if fmt == "gz":
        with open(source, "rb") as f_in:
            with gzip.open(str(output), "wb", compresslevel=level) as f_out:
                shutil.copyfileobj(f_in, f_out)
    elif fmt == "bz2":
        with open(source, "rb") as f_in:
            with bz2.open(str(output), "wb", compresslevel=level) as f_out:
                shutil.copyfileobj(f_in, f_out)
    elif fmt == "xz":
        with open(source, "rb") as f_in:
            with lzma.open(str(output), "wb", preset=level) as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        raise ValueError(f"file_compress: unsupported format: {fmt}")

    compressed_size = output.stat().st_size
    ratio_pct = round(compressed_size / source_size * 100, 2) if source_size > 0 else 0.0

    return {
        "output_path": str(output.resolve()),
        "source_size": source_size,
        "compressed_size": compressed_size,
        "ratio_pct": ratio_pct,
    }


@node(
    name="File Decompress",
    id="file_decompress",
    category="Files",
    icon="import",
    params={
        "source_path": {
            "description": "Compressed file to decompress.",
            "placeholder": "/path/to/file.txt.gz",
        },
        "output_path": {
            "description": "Output path.",
            "placeholder": "/path/to/file.txt",
        },
    },
)
def file_decompress(
    input: Any = None,
    source_path: str = "",
    output_path: str = "",
) -> dict[str, Any]:
    """Decompress a gzip, bzip2, or xz file."""
    if not source_path:
        raise ValueError("file_decompress: source_path is required")
    source = Path(source_path)
    if not source.exists():
        raise ValueError(f"file_decompress: file not found: {source_path}")
    if source.is_dir():
        raise ValueError("file_decompress: source is a directory, not a file")
    if not output_path:
        raise ValueError("file_decompress: output_path is required")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    name_lower = source.name.lower()
    if name_lower.endswith(".gz"):
        with gzip.open(str(source), "rb") as f_in:
            with open(str(output), "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    elif name_lower.endswith(".bz2"):
        with bz2.open(str(source), "rb") as f_in:
            with open(str(output), "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    elif name_lower.endswith((".xz", ".lzma")):
        with lzma.open(str(source), "rb") as f_in:
            with open(str(output), "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        raise ValueError(f"file_decompress: cannot detect compression format from: {source_path}")

    size_bytes = output.stat().st_size
    return {
        "output_path": str(output.resolve()),
        "size_bytes": size_bytes,
    }


__all__ = [
    "archive_create",
    "archive_extract",
    "archive_list",
    "file_compress",
    "file_decompress",
]
