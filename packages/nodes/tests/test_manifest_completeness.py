import nodyra_nodes  # noqa: F401 - importing registers the built-in nodes
from nodyra.sdk import registry


def test_manifest_completeness_for_visible_builtin_nodes() -> None:
    """Built-in palette entries must carry enough metadata for UI and AI builder use."""
    failures: list[str] = []
    for manifest in registry.manifests():
        if manifest.hidden:
            continue
        missing: list[str] = []
        if not manifest.description.strip():
            missing.append("description")
        if not manifest.category.strip() or manifest.category == "General":
            missing.append("specific category")
        if missing:
            failures.append(f"{manifest.id} ({manifest.name}): {', '.join(missing)}")

    assert not failures, "Incomplete built-in node manifests:\n" + "\n".join(failures)
