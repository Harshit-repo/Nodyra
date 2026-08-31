"""The curated template catalog is a product surface, not a fixture.

Templates are the fastest honest answer to "what can this do?", and the first
thing a new user runs. A template that fails validation, references a node that
was renamed, or needs a credential it never mentions costs exactly the user it
was written to win.

``scripts/validate_templates.py`` gates the schema and graph shape in CI. These
tests cover what that script cannot: that the catalog is big enough to be worth
browsing, that the credential-free promise is true, and that the ones a new user
meets first can actually run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import nodyra_nodes  # noqa: F401 — registers every node manifest
from nodyra.sdk import registry

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "templates"
PREVIEW_DIR = (
    Path(__file__).resolve().parents[3] / "apps" / "web" / "public" / "template-previews"
)


def _templates() -> list[tuple[str, dict]]:
    return [
        (path.stem, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(TEMPLATE_DIR.glob("*.json"))
    ]


ALL = _templates()
IDS = [name for name, _ in ALL]


def test_the_catalog_is_worth_browsing() -> None:
    """A gallery of four is not a gallery. This floor is deliberately low
    enough to never be the thing that blocks a merge, and high enough that
    silently dropping back to a handful fails."""
    assert len(ALL) >= 12, f"only {len(ALL)} templates in the catalog"


def test_every_template_is_discoverable_by_tag() -> None:
    """Tags are how the in-product gallery is filtered; an untagged template is
    reachable only by scrolling."""
    for name, template in ALL:
        assert template["tags"], f"{name}: no tags"


def test_starter_templates_exist_and_need_no_credentials() -> None:
    """The first thing a new user runs must work before they have configured
    anything. If every starter needs a Slack token, the first minute is spent
    in a credentials form instead of watching a workflow run."""
    starters = [t for _, t in ALL if "starter" in t["tags"]]
    assert len(starters) >= 3, "at least three credential-free starters expected"
    for template in starters:
        assert template["credential_free"], (
            f"{template['id']}: tagged starter but needs a credential"
        )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_credential_free_claims_are_true(name: str, template: dict) -> None:
    """A template that claims to need nothing, then asks for a credential, is
    the worst first impression the product can make."""
    if not template["credential_free"]:
        return
    for node in template["graph"]["nodes"]:
        params = node.get("params") or {}
        for key, value in params.items():
            if "credential" in key and value:
                pytest.fail(
                    f"{name}: claims credential_free but node {node['id']} "
                    f"sets {key}={value!r}"
                )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_every_node_type_still_exists(name: str, template: dict) -> None:
    """A renamed node silently breaks every template that used it, and the
    breakage only surfaces when a user clicks Use."""
    for node in template["graph"]["nodes"]:
        assert node["type"] in registry._nodes, (
            f"{name}: node {node['id']} uses unknown type {node['type']!r}"
        )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_declared_params_are_real(name: str, template: dict) -> None:
    """A param the node does not accept is silently ignored, so the template
    appears to configure something it does not."""
    for node in template["graph"]["nodes"]:
        node_def = registry._nodes[node["type"]]
        if node_def.accepts_var_keyword:
            continue
        declared = {p.name for p in node_def.manifest.params}
        supplied = set((node.get("params") or {}).keys())
        unknown = sorted(supplied - declared)
        assert not unknown, (
            f"{name}: node {node['id']} ({node['type']}) sets params the node "
            f"does not accept: {unknown}"
        )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_edges_reference_declared_ports(name: str, template: dict) -> None:
    """A branch wired to a port that does not exist takes no traffic — the
    template looks right on the canvas and does nothing at run time."""
    nodes = {n["id"]: n for n in template["graph"]["nodes"]}
    for edge in template["graph"].get("edges", []):
        assert edge["source"] in nodes, f"{name}: edge from unknown node {edge['source']}"
        assert edge["target"] in nodes, f"{name}: edge to unknown node {edge['target']}"
        handle = edge.get("sourceHandle")
        if not handle:
            continue
        outputs = {
            o.name for o in registry._nodes[nodes[edge["source"]]["type"]].manifest.outputs
        }
        assert handle in outputs, (
            f"{name}: edge leaves {edge['source']} by port {handle!r}, "
            f"but that node declares {sorted(outputs)}"
        )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_every_template_starts_somewhere(name: str, template: dict) -> None:
    """A graph with no entry point cannot be run, only admired."""
    targets = {e["target"] for e in template["graph"].get("edges", [])}
    roots = [n for n in template["graph"]["nodes"] if n["id"] not in targets]
    assert roots, f"{name}: every node has an inbound edge; there is no starting point"


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_previews_exist_and_are_svg(name: str, template: dict) -> None:
    """A missing thumbnail leaves a broken image in the gallery."""
    url = template["screenshot_url"]
    path = PREVIEW_DIR / url.removeprefix("/template-previews/")
    assert path.is_file(), f"{name}: preview missing at {url}"
    assert path.read_text(encoding="utf-8").lstrip().startswith("<svg"), (
        f"{name}: preview is not an SVG"
    )


@pytest.mark.parametrize("name,template", ALL, ids=IDS)
def test_descriptions_say_what_the_template_does(name: str, template: dict) -> None:
    assert len(template["description"]) >= 40, f"{name}: description is too thin"
    assert template["expected_result"], f"{name}: no expected_result"


def test_the_catalog_covers_more_than_one_kind_of_work() -> None:
    """Twelve variations on 'call an API' is one template, listed twelve times."""
    tags = {tag for _, t in ALL for tag in t["tags"]}
    assert len(tags) >= 12, f"catalog spans only {len(tags)} distinct tags: {sorted(tags)}"


def test_every_template_is_actually_committed():
    """A local checkout is not the product; the repository is.

    `.gitignore` carried a broad `data/` rule for runtime state, and
    `apps/api/app/data/templates/` matched it. Twelve generated templates sat on
    disk, `git add -A` said nothing — ignored files are not reported — and CI
    checked out three. Every test above passed locally and the catalogue
    shipped a quarter full.

    Counting files cannot catch that, because locally the files are there. The
    only question that distinguishes the two worlds is whether git tracks them.
    """
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "--", str(TEMPLATE_DIR)],
        capture_output=True,
        text=True,
        cwd=TEMPLATE_DIR,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")

    tracked = {
        Path(line).name
        for line in result.stdout.splitlines()
        if line.endswith(".json")
    }
    on_disk = {p.name for p in TEMPLATE_DIR.glob("*.json")}
    untracked = sorted(on_disk - tracked)

    assert not untracked, (
        f"{len(untracked)} template(s) exist locally but are not committed, so "
        f"they do not ship: {untracked}. Check .gitignore — a broad rule may be "
        f"swallowing them silently."
    )


def test_every_template_declares_compatibility_with_this_build():
    """A template that says it does not work here is a lie on the first screen.

    Every template shipped ``compatibility: ">=0.1.0,<0.2.0"`` while VERSION had
    moved to 1.0.0, so the New-workflow dialog told users "Compatible
    >=0.1.0,<0.2.0" on a 1.0.0 install — for all sixteen. Nothing enforces the
    range, so nothing broke; the product simply advertised itself as
    incompatible with itself.

    Pinned to the major version rather than the exact one, so a patch release
    does not invalidate the catalogue.
    """
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    current = Version(version_file.read_text(encoding="utf-8").strip())

    offenders = []
    for name, template in _templates():
        spec = str(template.get("compatibility") or "")
        if not spec:
            offenders.append(f"{name}: no compatibility declared")
            continue
        if current not in SpecifierSet(spec):
            offenders.append(f"{name}: {spec!r} excludes the current {current}")

    assert not offenders, (
        "templates advertise incompatibility with the build they ship in: "
        + "; ".join(offenders)
    )
