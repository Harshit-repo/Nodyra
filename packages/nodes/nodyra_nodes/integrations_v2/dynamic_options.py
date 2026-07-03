"""Dynamic option loader registry for v2 integration nodes.

Loaders are keyed by an arbitrary string id.  When the editor dropdown
requests options for a parameter whose ``load_options`` field names a loader
id, the API calls ``GET /nodes/dynamic-options/{loader_id}?<params>`` which
executes the registered loader and returns the choices.

Loaders receive arbitrary keyword arguments forwarded from the query string.
The API layer is responsible for decrypting credential values before calling
the loader.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

OptionLoader = Callable[..., list[dict[str, str]]]


@dataclass
class DynamicOption:
    """A single choice returned by a loader."""

    value: str
    label: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label, "description": self.description}


_loaders: dict[str, OptionLoader] = {}


def register_loader(loader_id: str, loader: OptionLoader) -> None:
    if loader_id in _loaders:
        raise ValueError(f"Duplicate dynamic option loader: {loader_id!r}")
    _loaders[loader_id] = loader


def get_loader(loader_id: str) -> OptionLoader:
    try:
        return _loaders[loader_id]
    except KeyError as exc:
        raise KeyError(f"Unknown dynamic option loader: {loader_id!r}") from exc


def list_loader_ids() -> list[str]:
    return list(_loaders.keys())


def call_loader(loader_id: str, **kwargs: Any) -> list[dict[str, str]]:
    """Execute a loader and normalise results to list[{value, label}]."""
    loader = get_loader(loader_id)
    results = loader(**kwargs)
    out: list[dict[str, str]] = []
    for item in results or []:
        if isinstance(item, DynamicOption):
            out.append(item.to_dict())
        elif isinstance(item, dict):
            out.append(
                {
                    "value": str(item.get("value", item.get("id", ""))),
                    "label": str(item.get("label", item.get("name", item.get("value", "")))),
                    "description": str(item.get("description", "")),
                }
            )
        else:
            out.append({"value": str(item), "label": str(item), "description": ""})
    return out
