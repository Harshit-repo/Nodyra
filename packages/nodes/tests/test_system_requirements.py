"""Tests for system_requirements on @node manifests."""
from __future__ import annotations
import noodle_nodes  # noqa: F401


def test_node_with_system_requirements_manifest() -> None:
    from noodle.sdk import node
    from noodle.sdk import NodeRegistry

    _reg = NodeRegistry()

    @node(
        name="TestSysReq",
        id="test_sysreq_node_phase2",
        registry=_reg,
        system_requirements=[
            {
                "name": "ghostscript",
                "apt": "ghostscript",
                "brew": "ghostscript",
                "windows": "https://ghostscript.com/releases",
                "dockerfile_hint": "RUN apt-get install -y ghostscript",
            }
        ],
    )
    def test_fn(input=None):
        return {}

    manifest = _reg.get("test_sysreq_node_phase2").manifest
    assert len(manifest.system_requirements) == 1
    sr = manifest.system_requirements[0]
    assert sr.name == "ghostscript"
    assert sr.apt == "ghostscript"
    assert sr.brew == "ghostscript"
    assert "ghostscript.com" in sr.windows
    assert "apt-get" in sr.dockerfile_hint


def test_node_without_system_requirements_defaults_to_empty() -> None:
    from noodle.sdk import node, NodeRegistry

    _reg = NodeRegistry()

    @node(name="TestNoSysReq", id="test_nosysreq_phase2", registry=_reg)
    def test_fn2(input=None):
        return {}

    manifest = _reg.get("test_nosysreq_phase2").manifest
    assert manifest.system_requirements == []


def test_system_requirement_all_fields_optional_except_name() -> None:
    from noodle.models import SystemRequirement
    sr = SystemRequirement(name="libssl")
    assert sr.name == "libssl"
    assert sr.apt == ""
    assert sr.brew == ""
    assert sr.windows == ""
    assert sr.dockerfile_hint == ""
    assert sr.note == ""
