"""Validate the curated workflow-template catalog without executing nodes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import nodyra_nodes  # noqa: F401 - registers manifests for graph validation
from nodyra.engine.validation import _validate_graph
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "apps" / "api" / "app" / "data" / "templates"
WEB_PUBLIC_DIR = ROOT / "apps" / "web" / "public"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REQUIRED = {
    "id",
    "name",
    "description",
    "tags",
    "version",
    "creator",
    "verified",
    "credential_free",
    "prerequisites",
    "expected_result",
    "permissions",
    "compatibility",
    "screenshot_url",
    "graph",
}


def validate_catalog() -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            template = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        missing = sorted(REQUIRED - set(template))
        if missing:
            errors.append(f"{path.name}: missing fields: {', '.join(missing)}")
            continue
        template_id = str(template["id"])
        if template_id in seen:
            errors.append(f"{path.name}: duplicate id {template_id!r}")
        seen.add(template_id)
        if not SEMVER.fullmatch(str(template["version"])):
            errors.append(f"{path.name}: version must be SemVer X.Y.Z")
        if not template["tags"] or not all(isinstance(item, str) for item in template["tags"]):
            errors.append(f"{path.name}: tags must be a non-empty string list")
        if not template["expected_result"]:
            errors.append(f"{path.name}: expected_result is required")
        screenshot = str(template["screenshot_url"])
        screenshot_path = WEB_PUBLIC_DIR / screenshot.lstrip("/")
        if not screenshot.startswith("/template-previews/") or not screenshot_path.is_file():
            errors.append(f"{path.name}: screenshot does not exist: {screenshot}")
        try:
            graph = WorkflowGraph.model_validate(template["graph"])
            _validate_graph(graph, registry)
            for node in graph.nodes:
                specs = {param.name: param for param in registry.get(node.type).manifest.params}
                for key, value in node.params.items():
                    spec = specs.get(key)
                    if spec is None:
                        errors.append(f"{path.name}: {node.id} has unknown parameter {key!r}")
                    elif spec.choices and "{{" not in str(value) and value not in spec.choices:
                        errors.append(
                            f"{path.name}: {node.id}.{key}={value!r} is not one of {spec.choices}"
                        )
        except Exception as exc:
            errors.append(f"{path.name}: invalid graph: {exc}")
    if not seen:
        errors.append("template catalog is empty")
    return errors


def main() -> int:
    errors = validate_catalog()
    if errors:
        print("Template catalog validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Validated {len(list(TEMPLATE_DIR.glob('*.json')))} workflow templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
