"""Agent config persistence under ``~/.noodle-runner/``.

``noodle-runner register`` writes ``config.json`` with the API URL, the
runner id, and the registration token; ``noodle-runner start`` reads it.
The data dir also holds the per-env venv cache (managed by env_manager).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def data_dir() -> Path:
    raw = os.environ.get("NOODLE_RUNNER_DATA", "~/.noodle-runner")
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


@dataclass
class AgentConfig:
    api_url: str
    runner_id: str
    token: str
    name: str = ""

    @property
    def ws_url(self) -> str:
        """Derive the WebSocket URL for this runner from the API URL."""
        base = self.api_url.rstrip("/")
        if base.startswith("https://"):
            scheme = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            scheme = "ws://" + base[len("http://"):]
        else:
            scheme = base
        return f"{scheme}/runner-pools/ws/runners/{self.runner_id}?token={self.token}"

    @property
    def artifact_upload_url(self) -> str:
        return f"{self.api_url.rstrip('/')}/runner-pools/artifact-upload"

    @property
    def wheel_index_url(self) -> str:
        """``--find-links`` page serving the unpublished noodle-* wheels."""
        return f"{self.api_url.rstrip('/')}/runner-pools/wheels/"


def save_config(cfg: AgentConfig) -> None:
    config_path().write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


def load_config() -> AgentConfig | None:
    path = config_path()
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AgentConfig(
        api_url=raw["api_url"],
        runner_id=raw["runner_id"],
        token=raw["token"],
        name=raw.get("name", ""),
    )
