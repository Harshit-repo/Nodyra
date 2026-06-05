"""Thin shim — re-exports from backends package for backward compatibility."""
from app.services.backends import build_environment, ensure_environment_ready
from app.services.backends.base import venv_dir
from app.services.backends.venv import VenvBackend, _do_build, venv_python

__all__ = [
    "build_environment",
    "ensure_environment_ready",
    "venv_dir",
    "venv_python",
    "VenvBackend",
    "_do_build",
]
