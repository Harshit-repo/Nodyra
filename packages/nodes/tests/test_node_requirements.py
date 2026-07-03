import nodyra_nodes  # noqa: F401 - registers built-in nodes
from nodyra.packages import canonical_package_name
from nodyra.sdk import registry


def test_duckdb_sql_node_requires_duckdb():
    ids = {m.id: m for m in registry.manifests()}
    duck = next(m for m in ids.values() if m.name == "DuckDB SQL")
    assert "duckdb" in {canonical_package_name(r) for r in duck.requirements}


def test_train_classifier_requires_sklearn_stack():
    ids = {m.name: m for m in registry.manifests()}
    reqs = {canonical_package_name(r) for r in ids["Train Classifier"].requirements}
    assert {"scikit-learn", "joblib", "pandas"} <= reqs


def test_chart_to_image_requires_cairosvg():
    ids = {m.name: m for m in registry.manifests()}
    reqs = {canonical_package_name(r) for r in ids["Chart To Image"].requirements}
    assert "cairosvg" in reqs


def test_plain_chart_node_has_no_requirements():
    ids = {m.name: m for m in registry.manifests()}
    assert ids["Chart"].requirements == []


def test_polars_transform_requires_polars():
    ids = {m.name: m for m in registry.manifests()}
    reqs = {canonical_package_name(r) for r in ids["Polars Transform"].requirements}
    assert "polars" in reqs
