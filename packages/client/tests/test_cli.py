from __future__ import annotations

import os
import stat

import pytest
from nodyra_client.cli import main as cli_main


def test_write_token_file_replaces_existing_file_atomically(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / ".nodyra" / "token"
    token_file.parent.mkdir()
    if os.name != "nt":
        token_file.parent.chmod(0o755)
    token_file.write_text("old")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)

    cli_main._write_token_file(b'{"token":"new"}')

    assert token_file.read_bytes() == b'{"token":"new"}'
    if os.name != "nt":
        assert stat.S_IMODE(token_file.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert not list(token_file.parent.glob("*.tmp"))


def test_write_token_file_replaces_symlink_instead_of_truncating_target(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "target"
    target.write_text("keep")
    token_file = tmp_path / ".nodyra" / "token"
    token_file.parent.mkdir()
    try:
        token_file.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)

    cli_main._write_token_file(b'{"token":"new"}')

    assert target.read_text() == "keep"
    assert token_file.read_bytes() == b'{"token":"new"}'
    assert not token_file.is_symlink()


def test_write_token_file_cleans_temp_file_on_write_error(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / ".nodyra" / "token"
    monkeypatch.setattr(cli_main, "_TOKEN_FILE", token_file)

    def fail_write(fd: int, payload: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(cli_main.os, "write", fail_write)

    with pytest.raises(OSError, match="disk full"):
        cli_main._write_token_file(b'{"token":"new"}')

    assert not token_file.exists()
    assert not list(token_file.parent.glob("*.tmp"))
