"""The pre-flight check must not demand packages every environment already has.

Found by clicking **Execute Workflow** on the starter template the getting-
started guide and the in-app activation checklist both send every new user to:

    This workflow's environment is missing packages required by its nodes:
    duckdb (needed by dataset, filter, limit, csv, records).

``duckdb`` was importable in the worker the whole time — version 1.5.3 — because
it is a hard dependency of ``nodyra-nodes``, which is installed in *every*
environment by construction. The run would have succeeded. The check refused it
because it compared node requirements against the environment's **declared**
package list and nothing else.

The cost is specific: the guide promises "no external credential" and the
checklist promises "setup cannot block the first result". Setup blocked the
first result, on the one path every new user takes.

The fix reads what ``nodyra-nodes`` actually depends on rather than hard-coding
a list, so it stays correct on its own: drop duckdb from that package and the
check immediately starts requiring it in the environment again.
"""

from __future__ import annotations

import importlib.metadata as metadata

import pytest

from app.services import package_preflight
from app.services.package_preflight import bundled_packages, find_missing_packages

DATASET_GRAPH = {
    "nodes": [
        {"id": "records", "type": "records_to_dataset"},
        {"id": "filter", "type": "dataset_filter"},
        {"id": "csv", "type": "csv_write"},
    ]
}


def test_nodyra_nodes_really_does_bundle_duckdb():
    """Guard the guard. If duckdb stops being a dependency of nodyra-nodes, the
    behaviour asserted below is wrong and this test says so first."""
    requires = metadata.requires("nodyra-nodes") or []
    names = {r.split(";")[0].split("[")[0].strip().split(" ")[0] for r in requires}
    names = {n.split(">")[0].split("=")[0].split("<")[0].split("!")[0] for n in names}
    assert "duckdb" in names, (
        "duckdb is no longer a dependency of nodyra-nodes; environments would "
        "genuinely need to declare it, and this suite's premise is stale"
    )


def test_bundled_packages_includes_the_node_librarys_dependencies():
    bundled = bundled_packages()
    assert "duckdb" in bundled
    assert len(bundled) > 5, f"suspiciously small bundled set: {sorted(bundled)}"


def test_a_bundled_package_is_not_reported_missing():
    """The bug, stated directly: an environment declaring only requests must
    still be able to run the dataset nodes."""
    missing = find_missing_packages(DATASET_GRAPH, ["requests"])
    assert missing == {}, (
        f"pre-flight still blocks the starter template: {missing}. duckdb ships "
        f"with nodyra-nodes and is importable in every environment."
    )


def test_a_genuinely_absent_package_is_still_reported():
    """The check must not become permissive: something neither declared nor
    bundled has to keep failing, or the guard is worthless."""
    graph = {"nodes": [{"id": "n1", "type": "__preflight_probe__"}]}

    class _Manifest:
        id = "__preflight_probe__"
        requirements = ["definitely-not-a-real-package>=1.0"]

    real = package_preflight.node_registry.manifests
    package_preflight.node_registry.manifests = lambda: [_Manifest()]
    try:
        missing = find_missing_packages(graph, ["requests"])
    finally:
        package_preflight.node_registry.manifests = real

    assert "definitely-not-a-real-package>=1.0" in missing
    assert missing["definitely-not-a-real-package>=1.0"] == ["n1"]


def test_an_explicitly_declared_package_still_satisfies_the_check():
    """Declaring a package must keep working, bundled or not."""
    graph = {"nodes": [{"id": "n1", "type": "__preflight_probe__"}]}

    class _Manifest:
        id = "__preflight_probe__"
        requirements = ["some-package>=2.0"]

    real = package_preflight.node_registry.manifests
    package_preflight.node_registry.manifests = lambda: [_Manifest()]
    try:
        assert find_missing_packages(graph, ["some-package"]) == {}
    finally:
        package_preflight.node_registry.manifests = real


@pytest.mark.parametrize("env_packages", [[], ["requests"], ["requests", "pandas"]])
def test_the_starter_template_runs_on_any_reasonable_environment(env_packages):
    """The specific promise being kept: the credential-free starter must not be
    blocked by whatever the default environment happens to declare."""
    assert find_missing_packages(DATASET_GRAPH, env_packages) == {}
