from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "generate_demo_env", REPO_ROOT / "scripts" / "generate_demo_env.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ensure_demo_env = _MODULE.ensure_demo_env
generated_values = _MODULE.generated_values
parse_env_file = _MODULE.parse_env_file
validate_values = _MODULE.validate_values


def test_demo_env_is_strong_persistent_and_rotatable(tmp_path: Path) -> None:
    env_path = tmp_path / "nested" / "demo.env"

    assert ensure_demo_env(env_path) is True
    first_text = env_path.read_text(encoding="utf-8")
    first = parse_env_file(env_path)

    assert first["NODYRA_BIND_HOST"] == "127.0.0.1"
    assert first["AUTH_REQUIRED"] == "false"
    assert first["POSTGRES_PASSWORD"] in first["DATABASE_URL"]
    assert first["NODYRA_SECRET_KEY"] == first["SECRET_KEY"]
    assert "nodyra-demo-secret" not in first_text
    assert all(
        len(first[key]) >= 32
        for key in (
            "POSTGRES_PASSWORD",
            "MINIO_ROOT_PASSWORD",
            "INTERNAL_API_TOKEN",
            "NODYRA_SECRET_KEY",
        )
    )
    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    assert ensure_demo_env(env_path) is False
    assert env_path.read_text(encoding="utf-8") == first_text

    assert ensure_demo_env(env_path, force=True) is True
    rotated = parse_env_file(env_path)
    assert rotated["POSTGRES_PASSWORD"] != first["POSTGRES_PASSWORD"]
    assert rotated["NODYRA_SECRET_KEY"] != first["NODYRA_SECRET_KEY"]


def test_demo_env_rejects_network_exposure() -> None:
    values = generated_values()
    values["NODYRA_BIND_HOST"] = "0.0.0.0"

    with pytest.raises(ValueError, match="loopback-only"):
        validate_values(values, source=Path("demo.env"))


def test_demo_env_rejects_invalid_existing_file(tmp_path: Path) -> None:
    env_path = tmp_path / "demo.env"
    env_path.write_text("AUTH_REQUIRED=false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required keys"):
        ensure_demo_env(env_path)
