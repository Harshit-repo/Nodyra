"""Archive extraction must not write outside its target directory.

Extracting an untrusted archive is one of the oldest sinks there is: a member
named ``../../etc/cron.d/x`` writes wherever the process can reach. Nodyra
guards it, but the guard compared resolved paths with ``str.startswith`` — which
treats ``/tmp/output-evil`` as inside ``/tmp/output``, because the second string
is a prefix of the first. A sibling directory whose name merely starts with the
target's name was reachable.

``archive_extract`` was one of the 100 nodes that had no test of any kind, which
is how a guard can look right and be wrong for years.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from nodyra_nodes.archive_nodes import _resolve_output, archive_extract

# ── The containment check itself ───────────────────────────────────────────


def test_a_normal_member_resolves_inside_the_target(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    assert _resolve_output(Path("data/report.csv"), target) == (
        target / "data" / "report.csv"
    )


def test_a_traversing_member_is_refused(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(ValueError, match="outside target directory"):
        _resolve_output(Path("../../etc/passwd"), target)


def test_an_absolute_member_is_refused(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(ValueError, match="outside target directory"):
        _resolve_output(Path("/etc/passwd"), target)


def test_a_sibling_directory_sharing_a_name_prefix_is_refused(tmp_path):
    """The bug this file was written for.

    With a prefix comparison, ``/tmp/out-evil/x`` counts as inside ``/tmp/out``
    because the target path is a literal prefix of it. Nothing about that is a
    parent/child relationship — the two directories are siblings.
    """
    target = tmp_path / "out"
    target.mkdir()
    (tmp_path / "out-evil").mkdir()

    with pytest.raises(ValueError, match="outside target directory"):
        _resolve_output(Path("../out-evil/planted.txt"), target)


def test_a_genuine_subdirectory_that_extends_the_name_is_allowed(tmp_path):
    """The fix must not overshoot: ``out/output/x`` really is inside ``out``."""
    target = tmp_path / "out"
    target.mkdir()
    resolved = _resolve_output(Path("output/deep/file.txt"), target)
    assert resolved == target / "output" / "deep" / "file.txt"


# ── End to end, through real archives ──────────────────────────────────────


def _zip_with(tmp_path: Path, members: dict[str, bytes]) -> str:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return str(path)


def _tar_with(tmp_path: Path, members: dict[str, bytes]) -> str:
    import io

    path = tmp_path / "archive.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def test_a_benign_zip_extracts(tmp_path):
    archive = _zip_with(tmp_path, {"a.txt": b"alpha", "sub/b.txt": b"beta"})
    out = tmp_path / "out"

    result = archive_extract(archive_path=archive, output_dir=str(out))

    assert result["count"] == 2
    assert (out / "a.txt").read_bytes() == b"alpha"
    assert (out / "sub" / "b.txt").read_bytes() == b"beta"


def test_a_traversing_zip_is_refused_before_anything_is_written(tmp_path):
    archive = _zip_with(tmp_path, {"../escaped.txt": b"pwned"})
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="outside target directory"):
        archive_extract(archive_path=archive, output_dir=str(out))

    assert not (tmp_path / "escaped.txt").exists()


def test_a_traversing_tar_is_refused(tmp_path):
    """Both code paths need the guard, not just the zip one."""
    archive = _tar_with(tmp_path, {"../escaped.txt": b"pwned"})
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="outside target directory"):
        archive_extract(archive_path=archive, output_dir=str(out))

    assert not (tmp_path / "escaped.txt").exists()


def test_a_zip_reaching_a_prefix_sibling_is_refused(tmp_path):
    """The end-to-end form of the prefix bug."""
    (tmp_path / "out-evil").mkdir()
    archive = _zip_with(tmp_path, {"../out-evil/planted.txt": b"pwned"})

    with pytest.raises(ValueError, match="outside target directory"):
        archive_extract(archive_path=archive, output_dir=str(tmp_path / "out"))

    assert not (tmp_path / "out-evil" / "planted.txt").exists()


def test_missing_inputs_fail_clearly(tmp_path):
    with pytest.raises(ValueError, match="archive_path is required"):
        archive_extract(archive_path="", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="not found"):
        archive_extract(archive_path=str(tmp_path / "nope.zip"), output_dir=str(tmp_path))
    archive = _zip_with(tmp_path, {"a.txt": b"x"})
    with pytest.raises(ValueError, match="output_dir is required"):
        archive_extract(archive_path=archive, output_dir="")
