"""System utility nodes."""

from __future__ import annotations

import json
import math
import secrets
import socket
import string
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from noodle.sdk import node


def _psutil():
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "System info requires psutil. Install: uv pip install psutil"
        ) from exc
    return psutil


_CONFUSING = frozenset("il1o0O")


def _parse_items(items: str) -> list[str]:
    if not items or not items.strip():
        return []
    items = items.strip()
    if items.startswith("["):
        try:
            parsed = json.loads(items)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [line.strip() for line in items.split("\n") if line.strip()]


@node(
    name="System Info",
    id="system_info",
    category="System",
    icon="terminal",
    tool_side_effecting=False,
)
def system_info(input: Any = None) -> dict[str, Any]:
    """Collect system resource information."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return {
            "cpu": {},
            "memory": {},
            "disk": {},
            "host": {
                "hostname": socket.gethostname(),
                "platform": __import__("platform").platform(),
            },
            "load_avg": None,
            "_message": "psutil not installed. Install: uv pip install psutil",
        }

    result: dict[str, Any] = {}

    try:
        result["cpu"] = {
            "percent": psutil.cpu_percent(interval=0.1),
            "cores_physical": psutil.cpu_count(logical=False) or 0,
            "cores_logical": psutil.cpu_count(logical=True) or 0,
            "frequency_mhz": (psutil.cpu_freq().current if psutil.cpu_freq() else 0.0),
        }
    except (PermissionError, RuntimeError):
        result["cpu"] = {}

    try:
        mem = psutil.virtual_memory()
        result["memory"] = {
            "total_gb": round(mem.total / (1024**3), 1),
            "available_gb": round(mem.available / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "percent": mem.percent,
        }
    except (PermissionError, RuntimeError):
        result["memory"] = {}

    try:
        du = psutil.disk_usage("/")
        result["disk"] = {
            "total_gb": round(du.total / (1024**3), 1),
            "used_gb": round(du.used / (1024**3), 1),
            "free_gb": round(du.free / (1024**3), 1),
            "percent": du.percent,
        }
    except (PermissionError, RuntimeError):
        result["disk"] = {}

    try:
        import platform as _platform

        boot_ts = psutil.boot_time()
        result["host"] = {
            "hostname": socket.gethostname(),
            "platform": _platform.system(),
            "platform_version": _platform.version(),
            "uptime_hours": round((time.time() - boot_ts) / 3600, 1),
            "boot_time": datetime.fromtimestamp(boot_ts, tz=timezone.utc).isoformat(),
        }
    except (PermissionError, RuntimeError, OSError):
        result["host"] = {"hostname": socket.gethostname()}

    try:
        result["load_avg"] = [round(x, 1) for x in psutil.getloadavg()]
    except (PermissionError, RuntimeError, OSError):
        result["load_avg"] = None

    return result


@node(
    name="System Sleep",
    id="system_sleep",
    category="System",
    icon="clock",
    params={
        "seconds": {
            "description": "Seconds to sleep.",
            "placeholder": "1.0",
        },
    },
)
def system_sleep(input: Any = None, seconds: float = 1.0) -> dict[str, Any]:
    """Pause execution for a given number of seconds."""
    seconds = max(0.0, float(seconds))
    time.sleep(seconds)
    return {
        "slept_seconds": seconds,
        "awake_at": datetime.now(timezone.utc).isoformat(),
    }


@node(
    name="System List Processes",
    id="system_list_processes",
    category="System",
    icon="terminal",
    tool_side_effecting=False,
    params={
        "filter_name": {
            "group": "Options",
            "description": "Filter by process name (partial match, case-insensitive).",
            "placeholder": "python",
        },
        "sort_by": {
            "group": "Options",
            "choices": ["cpu_percent", "memory_percent", "name", "pid"],
            "description": "Sort field.",
        },
        "limit": {
            "group": "Options",
            "description": "Max results.",
            "placeholder": "20",
        },
    },
)
def system_list_processes(
    input: Any = None,
    filter_name: str = "",
    sort_by: str = "memory_percent",
    limit: int = 20,
) -> dict[str, Any]:
    """List running system processes."""
    psutil = _psutil()
    limit = max(1, min(1000, int(limit or 20)))
    valid_sort = ("cpu_percent", "memory_percent", "name", "pid")
    sort_by = sort_by if sort_by in valid_sort else "memory_percent"

    procs: list[dict[str, Any]] = []
    for p in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent", "status", "create_time"]
    ):
        try:
            pinfo = p.info
            name = pinfo.get("name") or ""
            if filter_name and filter_name.lower() not in name.lower():
                continue
            created_ts = pinfo.get("create_time")
            procs.append(
                {
                    "pid": pinfo.get("pid"),
                    "name": name,
                    "cpu_percent": pinfo.get("cpu_percent") or 0.0,
                    "memory_percent": pinfo.get("memory_percent") or 0.0,
                    "status": pinfo.get("status") or "",
                    "created": (
                        datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
                        if created_ts
                        else ""
                    ),
                }
            )
        except (PermissionError, psutil.NoSuchProcess):
            continue

    reverse = sort_by in ("cpu_percent", "memory_percent", "pid")
    procs.sort(
        key=lambda x: (
            x.get(sort_by) or 0
            if isinstance(x.get(sort_by), (int, float))
            else str(x.get(sort_by, ""))
        ),
        reverse=reverse,
    )
    procs = procs[:limit]

    return {
        "processes": procs,
        "count": len(procs),
    }


@node(
    name="UUID Generate",
    id="uuid_generate",
    category="System",
    icon="hash",
    tool_side_effecting=False,
    params={
        "version": {
            "choices": ["4", "7"],
            "description": "UUID version (4=random, 7=time-ordered).",
        },
        "count": {
            "group": "Options",
            "description": "Number of UUIDs to generate.",
            "placeholder": "1",
        },
    },
)
def uuid_generate(input: Any = None, version: str = "4", count: int = 1) -> dict[str, Any]:
    """Generate UUIDs (version 4 or 7)."""
    count = max(1, min(1000, int(count or 1)))

    use_uuid7 = version == "7"
    if use_uuid7:
        try:
            uuid.uuid7()
        except AttributeError:
            use_uuid7 = False

    uuids: list[str] = []
    gen = uuid.uuid7 if use_uuid7 else uuid.uuid4
    for _ in range(count):
        uuids.append(str(gen()))

    return {
        "uuids": uuids,
        "count": len(uuids),
    }


@node(
    name="Random Password",
    id="random_password",
    category="System",
    icon="hash",
    tool_side_effecting=False,
    params={
        "length": {
            "description": "Password length.",
            "placeholder": "16",
        },
        "include_lowercase": {
            "group": "Options",
        },
        "include_uppercase": {
            "group": "Options",
        },
        "include_digits": {
            "group": "Options",
        },
        "include_symbols": {
            "group": "Options",
            "description": "Include special chars like !@#$%^&*",
        },
        "exclude_confusing": {
            "group": "Options",
            "description": "Exclude confusing chars like il1o0O.",
        },
    },
)
def random_password(
    input: Any = None,
    length: int = 16,
    include_lowercase: bool = True,
    include_uppercase: bool = True,
    include_digits: bool = True,
    include_symbols: bool = False,
    exclude_confusing: bool = True,
) -> dict[str, Any]:
    """Generate a cryptographically secure random password."""
    length = max(4, min(128, int(length or 16)))

    chars = ""
    if include_lowercase:
        chars += string.ascii_lowercase
    if include_uppercase:
        chars += string.ascii_uppercase
    if include_digits:
        chars += string.digits
    if include_symbols:
        chars += string.punctuation
    if exclude_confusing:
        chars = "".join(c for c in chars if c not in _CONFUSING)
    if not chars:
        raise ValueError("At least one character set must be selected")

    password = "".join(secrets.choice(chars) for _ in range(length))
    entropy = round(length * math.log2(len(chars)), 1)

    return {
        "password": password,
        "length": length,
        "entropy_bits": entropy,
    }


@node(
    name="Batch Process",
    id="batch_process",
    category="System",
    icon="terminal",
    params={
        "items": {
            "description": "JSON array to split into batches, or multiline list.",
            "multiline": True,
        },
        "batch_size": {
            "description": "Items per batch.",
            "placeholder": "10",
        },
    },
)
def batch_process(
    input: Any = None, items: str = "", batch_size: int = 10
) -> dict[str, Any]:
    """Split a list of items into batches."""
    batch_size = max(1, int(batch_size or 10))
    parsed = _parse_items(items)

    batches: list[list[str]] = []
    for i in range(0, len(parsed), batch_size):
        batches.append(parsed[i : i + batch_size])

    return {
        "batches": batches,
        "total_items": len(parsed),
        "batch_count": len(batches),
        "batch_size": batch_size,
    }


@node(
    name="System Env Var",
    id="system_env_var",
    category="System",
    icon="terminal",
    tool_side_effecting=False,
    params={
        "name": {
            "description": "Environment variable name.",
            "placeholder": "PATH",
        },
        "default": {
            "group": "Options",
            "description": "Default value if not set.",
            "placeholder": "",
        },
    },
)
def system_env_var(
    input: Any = None,
    name: str = "",
    default: str = "",
) -> dict[str, Any]:
    """Get the value of an environment variable."""
    if not name:
        raise ValueError("system_env_var: name is required")
    value = os.environ.get(name, default)
    return {"name": name, "value": value, "found": name in os.environ}


@node(
    name="System Disk Usage",
    id="system_disk_usage",
    category="System",
    icon="terminal",
    tool_side_effecting=False,
    params={
        "path": {
            "description": "Path to check disk usage for.",
            "placeholder": "/",
        },
    },
)
def system_disk_usage(
    input: Any = None,
    path: str = "/",
) -> dict[str, Any]:
    """Get disk usage statistics for a given path."""
    if not path:
        raise ValueError("system_disk_usage: path is required")

    psutil = _psutil()
    try:
        du = psutil.disk_usage(path)
    except PermissionError as exc:
        raise PermissionError(f"system_disk_usage: permission denied for '{path}'") from exc
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"system_disk_usage: path not found: {path}") from exc

    return {
        "path": path,
        "total_gb": round(du.total / (1024**3), 1),
        "used_gb": round(du.used / (1024**3), 1),
        "free_gb": round(du.free / (1024**3), 1),
        "percent": du.percent,
    }


__all__ = [
    "system_info",
    "system_sleep",
    "system_list_processes",
    "uuid_generate",
    "random_password",
    "batch_process",
    "system_env_var",
    "system_disk_usage",
]
