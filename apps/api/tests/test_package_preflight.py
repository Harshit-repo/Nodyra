"""Tests for package_preflight.py — PEP 508 marker awareness."""
import sys
from unittest.mock import patch

from app.services.package_preflight import _marker_applies, find_missing_packages


def test_marker_applies_eq_match() -> None:
    assert _marker_applies(f"sys_platform == '{sys.platform}'") is True


def test_marker_applies_eq_no_match() -> None:
    other = "linux" if sys.platform != "linux" else "win32"
    assert _marker_applies(f"sys_platform == '{other}'") is False


def test_marker_applies_ne_match() -> None:
    other = "linux" if sys.platform != "linux" else "win32"
    assert _marker_applies(f"sys_platform != '{other}'") is True


def test_marker_applies_ne_no_match() -> None:
    assert _marker_applies(f"sys_platform != '{sys.platform}'") is False


def test_marker_applies_unknown_returns_true() -> None:
    assert _marker_applies("python_version >= '3.9'") is True


def test_find_missing_packages_skips_wrong_platform_req() -> None:
    """A requirement marked for a different platform must not appear as missing."""
    other_platform = "linux" if sys.platform != "linux" else "win32"

    fake_manifests = [
        type("M", (), {
            "id": "my_node",
            "requirements": [f"some-pkg>=1.0; sys_platform=='{other_platform}'"],
        })()
    ]

    graph = {"nodes": [{"type": "my_node", "id": "n1"}]}
    with patch("app.services.package_preflight.node_registry") as mock_reg:
        mock_reg.manifests.return_value = fake_manifests
        result = find_missing_packages(graph, env_packages=[])

    assert result == {}, f"Expected no missing packages but got {result}"


def test_find_missing_packages_includes_matching_platform_req() -> None:
    """A requirement whose marker matches the current platform IS reported missing."""
    fake_manifests = [
        type("M", (), {
            "id": "my_node",
            "requirements": [f"some-pkg>=1.0; sys_platform=='{sys.platform}'"],
        })()
    ]

    graph = {"nodes": [{"type": "my_node", "id": "n1"}]}
    with patch("app.services.package_preflight.node_registry") as mock_reg:
        mock_reg.manifests.return_value = fake_manifests
        result = find_missing_packages(graph, env_packages=[])

    assert f"some-pkg>=1.0; sys_platform=='{sys.platform}'" in result
