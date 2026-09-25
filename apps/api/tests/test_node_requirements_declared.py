"""A node that needs a package must say so, or pre-flight cannot protect it.

Pre-flight blocks a run when a node's declared ``requirements`` are missing
from the workflow's environment, and says which packages to add. A node that
imports something and never declares it slips straight past that check and
fails deep in execution instead:

    write_excel_file -> RuntimeError: openpyxl is required: pip install openpyxl

Found by running the shipped ``scheduled_dataset_snapshot`` template, which
uses that node. Compare ``data_quality_gate``, whose node *does* declare
``ydata-profiling``, and which is refused up front with a message naming the
package and where to add it. Same missing package, two very different
experiences, decided entirely by whether someone remembered the declaration.

Twelve nodes were in that state.

Nodes that degrade gracefully are exempt and listed below: for them the package
is genuinely optional, and declaring it would block runs that succeed today.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from app.services.package_preflight import bundled_packages
from nodyra.sdk import registry

NODES_ROOT = Path(__file__).resolve().parents[3] / "packages" / "nodes" / "nodyra_nodes"

# Import name -> distribution name, where they differ.
IMPORT_TO_DISTRIBUTION = {
    "PIL": "pillow", "sklearn": "scikit-learn", "docx": "python-docx",
    "barcode": "python-barcode", "bs4": "beautifulsoup4", "cv2": "opencv-python",
    "yaml": "pyyaml", "dateutil": "python-dateutil", "fitz": "pymupdf",
    "pptx": "python-pptx", "serial": "pyserial", "OpenSSL": "pyopenssl",
    "magic": "python-magic", "jwt": "pyjwt", "gnupg": "python-gnupg",
    "snowflake": "snowflake-connector-python",
}

# ``google`` and ``azure`` are namespace packages: the import root names no
# distribution at all, so these are resolved by dotted prefix instead.
NAMESPACE_ROOTS = {"google", "azure"}
DOTTED_PREFIX_TO_DISTRIBUTION = {
    "google.cloud.bigquery": "google-cloud-bigquery",
    "google.cloud.storage": "google-cloud-storage",
    "google.oauth2": "google-auth",
    "google.auth": "google-auth",
    "azure.storage.blob": "azure-storage-blob",
    "azure.cognitiveservices.speech": "azure-cognitiveservices-speech",
    "azure.identity": "azure-identity",
}

# Nodes that catch ImportError and carry on with reduced behaviour. The package
# is optional for them, so declaring it would make pre-flight refuse runs that
# work. Each entry is a deliberate decision, not an oversight.
DEGRADES_GRACEFULLY = {
    "date_parse_normalize",  # falls back to built-in date parsing
    "record_linkage",        # falls back to difflib.SequenceMatcher
    "system_info",           # returns a message instead of raising
    # spaCy is only used by the optional "spacy" backend (default "llm" works
    # without it) and raises an ImportError with install instructions.
    "ai_named_entity_recognition",
}

FIRST_PARTY = {"nodyra", "nodyra_nodes", "app"}


def _normalise(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _declared_for(manifest) -> set[str]:
    out = set()
    for req in manifest.requirements or []:
        base = req.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        if base:
            out.add(_normalise(base))
    return out


def _third_party_imports(fn: ast.FunctionDef) -> set[str]:
    """Imported module paths, keeping enough of the path to name a distribution.

    A plain root is enough for ordinary packages. For a namespace package the
    root names nothing, so the dotted path is kept and resolved by prefix.
    """
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            module = node.module
            if module.split(".")[0] in NAMESPACE_ROOTS:
                # ``from google.cloud import storage`` - the imported name is
                # the part that identifies the distribution.
                names.update(f"{module}.{alias.name}" for alias in node.names)
            names.add(module)
    kept = set()
    for name in names:
        root = name.split(".")[0]
        if root in sys.stdlib_module_names or root in FIRST_PARTY or root.startswith("_"):
            continue
        kept.add(name if root in NAMESPACE_ROOTS else root)
    return kept


def _distribution_for(imported: str) -> str:
    """Best-known distribution name for an imported module path."""
    if imported.split(".")[0] in NAMESPACE_ROOTS:
        for prefix, dist in DOTTED_PREFIX_TO_DISTRIBUTION.items():
            if imported == prefix or imported.startswith(prefix + "."):
                return dist
        # A bare namespace prefix ("google.cloud") names no distribution. A
        # sibling import in the same node carries the specific path, so there
        # is nothing to check here.
        return ""
    return IMPORT_TO_DISTRIBUTION.get(imported, imported)


def _node_functions():
    """Every @node-decorated function, paired with its registered manifest."""
    by_id = {m.id: m for m in registry.manifests()}
    for path in sorted(NODES_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for dec in fn.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                for kw in dec.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        manifest = by_id.get(kw.value.value)
                        if manifest is not None:
                            yield kw.value.value, fn, manifest


def test_the_scan_finds_nodes_at_all():
    """Guard the guard: if the AST walk stops matching, every assertion below
    would pass by checking nothing."""
    found = list(_node_functions())
    assert len(found) > 200, f"only {len(found)} node functions found"


def test_every_hard_dependency_is_declared():
    bundled = {_normalise(b) for b in bundled_packages()}
    gaps: list[str] = []

    for node_id, fn, manifest in _node_functions():
        if node_id in DEGRADES_GRACEFULLY:
            continue
        declared = _declared_for(manifest)
        needed: dict[str, str] = {}
        for imported in sorted(_third_party_imports(fn)):
            distribution = _normalise(_distribution_for(imported))
            if not distribution or distribution in declared or distribution in bundled:
                continue
            needed.setdefault(distribution, imported)
        for distribution, imported in sorted(needed.items()):
            gaps.append(
                f"{node_id} needs {distribution!r} (imports {imported!r}) "
                f"but declares {sorted(declared) or 'nothing'}"
            )

    assert not gaps, (
        "these nodes import a package that is neither bundled nor declared, so "
        "pre-flight cannot warn about it and the run fails mid-execution:\n  "
        + "\n  ".join(gaps)
    )


@pytest.mark.parametrize("node_id", sorted(DEGRADES_GRACEFULLY))
def test_exempt_nodes_really_do_degrade(node_id: str):
    """The exemption list must stay honest. A node listed here has to actually
    catch the ImportError - otherwise it belongs in the check above."""
    for found_id, fn, _ in _node_functions():
        if found_id != node_id:
            continue
        handlers = [
            handler
            for try_node in ast.walk(fn)
            if isinstance(try_node, ast.Try)
            for handler in try_node.handlers
            if any(isinstance(n, ast.Import | ast.ImportFrom) for n in ast.walk(try_node))
        ]
        assert handlers, (
            f"{node_id} is exempt as degrading gracefully but has no try/except "
            f"around an import"
        )
        return
    pytest.fail(f"{node_id} is on the exemption list but is not a registered node")
