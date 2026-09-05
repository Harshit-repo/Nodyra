"""A template that says it needs no setup must actually need no setup.

Templates carry a ``credential_free`` flag. The in-app activation checklist
sends a new user straight at it, promising:

    "Choose a credential-free template - start with deterministic sample data
     so setup cannot block the first result."

Three templates claimed the flag and could not run out of the box, because they
read a file the user does not have:

    csv_clean_dedupe   read_csv_file        path=data/input.csv
    data_quality_gate  read_csv_file        path=data/metrics.csv
    pdf_text_index     pdf_extract_text_v2  path=data/document.pdf

    FileNotFoundError: [Errno 2] No such file or directory: 'data\input.csv'

Found by instantiating all 16 shipped templates and running them. The flag is
what the first-run journey is built on, so a false one breaks exactly the
person with the least context to recover.

Two ways a template can break the promise, and both are checked:

  1. it reads a filesystem path that nothing in the graph produces
  2. it uses a node whose requirements are not bundled, so pre-flight refuses
     the run until the operator installs a package

Network access is not treated as setup: the working credential-free templates
(api_poll_transform, rss_digest, json_api_to_csv) all fetch a public URL, and
that is the point of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "templates"

# Params naming a file the node READS. Writers use "filename" and produce their
# own artifact, so they are not a prerequisite.
READ_PATH_PARAMS = {"path", "source_path", "input_path"}

# A trigger's "path" is a URL route, not a file on disk - webhook_trigger's
# path=incoming-event is the endpoint the caller posts to.
def _reads_from_disk(node: dict) -> bool:
    return not str(node.get("type", "")).endswith("_trigger")


def _templates():
    if not TEMPLATE_DIR.exists():
        pytest.skip("shipped templates are not present in this checkout")
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        yield json.loads(path.read_text(encoding="utf-8"))


def test_there_are_templates_to_check():
    """Guard the guard: an empty glob would make everything below vacuous."""
    assert len(list(_templates())) >= 10


@pytest.mark.parametrize(
    "template", [t for t in _templates() if t.get("credential_free")],
    ids=lambda t: t.get("id", "?"),
)
def test_credential_free_templates_read_no_files(template):
    reads = [
        f"{node['id']}.{param}={value!r}"
        for node in template["graph"]["nodes"]
        if _reads_from_disk(node)
        for param, value in (node.get("params") or {}).items()
        if param in READ_PATH_PARAMS and str(value or "").strip()
    ]
    assert not reads, (
        f"{template['id']} is marked credential_free but reads a file the user "
        f"does not have: {reads}. Either give it inline sample data, the way "
        f"dataset_filter_export does, or drop the flag."
    )


@pytest.mark.parametrize(
    "template", [t for t in _templates() if t.get("credential_free")],
    ids=lambda t: t.get("id", "?"),
)
def test_credential_free_templates_need_no_extra_packages(template):
    from nodyra.sdk import registry

    from app.services.package_preflight import bundled_packages

    bundled = {b.lower().replace("-", "_") for b in bundled_packages()}
    by_id = {m.id: m for m in registry.manifests()}

    needed: list[str] = []
    for node in template["graph"]["nodes"]:
        manifest = by_id.get(node.get("type", ""))
        for req in (manifest.requirements if manifest else []) or []:
            base = req.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
            if base.lower().replace("-", "_") not in bundled:
                needed.append(f"{node['id']} ({node.get('type')}) needs {req}")

    assert not needed, (
        f"{template['id']} is marked credential_free but pre-flight will refuse "
        f"the run until packages are installed: {needed}"
    )
