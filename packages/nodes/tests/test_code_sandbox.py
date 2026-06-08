"""Security tests for the Code node sandbox.

Verifies that _run_code_isolated blocks dangerous module imports while
allowing legitimate ones, and that the AST validator still catches
blocked names.
"""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle_nodes.builtin import _run_code_isolated


def test_code_node_blocks_subprocess_import() -> None:
    """subprocess must not be importable from inside the code sandbox."""
    with pytest.raises(ImportError, match="not available in the code sandbox"):
        _run_code_isolated(None, "import subprocess")


def test_code_node_blocks_subprocess_via_from_import() -> None:
    """'from subprocess import ...' is also blocked."""
    with pytest.raises(ImportError, match="not available in the code sandbox"):
        _run_code_isolated(None, "from subprocess import run")


def test_code_node_blocks_pty_import() -> None:
    """pty (pseudo-terminal, process spawning) must not be importable."""
    with pytest.raises(ImportError, match="not available in the code sandbox"):
        _run_code_isolated(None, "import pty")


def test_code_node_blocks_ctypes_import() -> None:
    """ctypes (arbitrary C calls) must not be importable."""
    with pytest.raises(ImportError, match="not available in the code sandbox"):
        _run_code_isolated(None, "import ctypes")


def test_code_node_blocks_multiprocessing_import() -> None:
    """multiprocessing must not be importable (process spawning)."""
    with pytest.raises(ImportError, match="not available in the code sandbox"):
        _run_code_isolated(None, "import multiprocessing")


def test_code_node_allows_json_import() -> None:
    """json is a standard safe library and must remain importable."""
    result = _run_code_isolated(None, "import json\noutput = json.dumps({'k': 1})")
    assert result == '{"k": 1}'


def test_code_node_allows_re_import() -> None:
    """re (regex) is safe and must remain importable."""
    result = _run_code_isolated(None, "import re\noutput = bool(re.match(r'\\d+', '42'))")
    assert result is True


def test_code_node_allows_math_import() -> None:
    """math is safe and must remain importable."""
    result = _run_code_isolated(None, "import math\noutput = math.floor(3.9)")
    assert result == 3


def test_code_node_allows_datetime_import() -> None:
    """datetime is safe and must remain importable."""
    result = _run_code_isolated(
        None,
        "from datetime import date\noutput = str(date(2024, 1, 1))",
    )
    assert result == "2024-01-01"
