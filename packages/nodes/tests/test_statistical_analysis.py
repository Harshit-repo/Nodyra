"""Tests for statistical analysis nodes."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

import nodyra_nodes  # noqa: F401 — registers nodes
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.datasets import is_dataset_ref


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="stat-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("stat-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)

# Shared fixture data
ROWS_NUMERIC = [
    {"value": 2.1, "group": "A", "value2": 2.3},
    {"value": 3.4, "group": "A", "value2": 3.1},
    {"value": 2.8, "group": "A", "value2": 2.9},
    {"value": 5.1, "group": "B", "value2": 5.0},
    {"value": 6.2, "group": "B", "value2": 6.3},
    {"value": 5.8, "group": "B", "value2": 5.7},
]

ROWS_FEATURES = [
    {"f1": float(i), "f2": float(i * 2), "f3": float(i * 3)} for i in range(20)
]

ROWS_TS = [
    {"date": f"2024-{(i % 12) + 1:02d}-01", "value": float(i) + (i % 4) * 2.0}
    for i in range(24)
]


# ---------------------------------------------------------------------------
# Import-safety
# ---------------------------------------------------------------------------


def test_statistical_analysis_importable_without_optional_packages() -> None:
    import nodyra_nodes.statistical_analysis as mod

    for node_id in [
        "statistical_test",
        "distribution_fit",
        "correlation_analysis",
        "regression_analysis",
        "monte_carlo_simulate",
        "bootstrap_ci",
        "optimization_solve",
        "time_series_decompose",
        "dimensionality_reduce",
    ]:
        assert hasattr(mod, node_id), f"missing {node_id}"


def test_import_does_not_import_optional_packages() -> None:
    # Run in a clean interpreter: a same-process sys.modules snapshot is polluted
    # by other test modules that legitimately import scipy/sklearn (TEST-1). This
    # checks the real intent — importing the node module must not eagerly pull in
    # the heavy optional deps.
    import subprocess

    code = (
        "import sys, nodyra_nodes.statistical_analysis;"
        "leaked = {'scipy','statsmodels','pulp','sklearn'} & set(sys.modules);"
        "assert not leaked, leaked"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# statistical_test
# ---------------------------------------------------------------------------


def test_statistical_test_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises((ValueError, TypeError)):
        statistical_test(input=None, test="t_test_1samp", column="value")


def test_statistical_test_raises_without_column() -> None:
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises(ValueError, match="column"):
        statistical_test(input=ROWS_NUMERIC, test="t_test_1samp", column="")


def test_statistical_test_ttest_1samp() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC, test="t_test_1samp", column="value", alpha=0.05
    )
    assert "statistic" in result
    assert "p_value" in result
    assert "reject_null" in result
    assert isinstance(result["reject_null"], bool)
    assert "interpretation" in result
    assert result["n"] == len(ROWS_NUMERIC)


def test_statistical_test_ttest_ind_with_group() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC,
        test="t_test_ind",
        column="value",
        group_column="group",
        alpha=0.05,
    )
    assert result["reject_null"] is True  # groups A (~2.7) and B (~5.7) are clearly different


def test_statistical_test_ttest_ind_with_second_column() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC,
        test="t_test_ind",
        column="value",
        second_column="value2",
        alpha=0.05,
    )
    assert "p_value" in result


def test_statistical_test_anova() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC,
        test="anova",
        column="value",
        group_column="group",
        alpha=0.05,
    )
    assert result["statistic"] > 0
    assert "interpretation" in result


def test_statistical_test_mann_whitney() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC,
        test="mann_whitney",
        column="value",
        group_column="group",
        alpha=0.05,
    )
    assert "p_value" in result


def test_statistical_test_ks_2samp() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC,
        test="ks_2samp",
        column="value",
        second_column="value2",
        alpha=0.05,
    )
    assert "statistic" in result


def test_statistical_test_shapiro_wilk() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input=ROWS_NUMERIC, test="shapiro_wilk", column="value"
    )
    assert "statistic" in result
    assert "p_value" in result


def test_statistical_test_small_sample_warning() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    small = [{"value": float(i)} for i in range(5)]
    result = statistical_test(input=small, test="t_test_1samp", column="value")
    assert "warnings" in result
    assert any("Small sample" in w for w in result["warnings"])


def test_statistical_test_unknown_test_raises() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises(ValueError, match="Unknown test"):
        statistical_test(input=ROWS_NUMERIC, test="bogus_test", column="value")


def test_statistical_test_raises_missing_scipy() -> None:
    with patch.dict(sys.modules, {"scipy": None, "scipy.stats": None}):
        from nodyra_nodes.statistical_analysis import statistical_test

        with pytest.raises((RuntimeError, ImportError)):
            statistical_test(
                input=ROWS_NUMERIC, test="t_test_1samp", column="value"
            )


# ---------------------------------------------------------------------------
# distribution_fit
# ---------------------------------------------------------------------------


def test_distribution_fit_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import distribution_fit

    with pytest.raises((ValueError, TypeError)):
        distribution_fit(input=None, column="value")


def test_distribution_fit_raises_without_column() -> None:
    from nodyra_nodes.statistical_analysis import distribution_fit

    with pytest.raises(ValueError, match="column"):
        distribution_fit(input=ROWS_NUMERIC, column="")


def test_distribution_fit_returns_dataset_ref(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import distribution_fit

    result = distribution_fit(
        input=ROWS_NUMERIC, column="value", distributions="norm,expon"
    )
    assert is_dataset_ref(result["dataset"])
    assert result["distributions_tested"] >= 1
    assert result["n"] == len(ROWS_NUMERIC)


def test_distribution_fit_skips_bad_distribution_name(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import distribution_fit

    result = distribution_fit(
        input=ROWS_NUMERIC,
        column="value",
        distributions="norm,totally_fake_distribution_xyz",
    )
    assert result["distributions_tested"] >= 1  # norm succeeded


def test_distribution_fit_sorted_by_ks_statistic(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.datasets import materialize_dataset
    from nodyra_nodes.statistical_analysis import distribution_fit

    result = distribution_fit(
        input=ROWS_NUMERIC, column="value", distributions="norm,expon,lognorm"
    )
    rows = materialize_dataset(result["dataset"])
    ks_vals = [r["ks_statistic"] for r in rows]
    assert ks_vals == sorted(ks_vals)


# ---------------------------------------------------------------------------
# correlation_analysis
# ---------------------------------------------------------------------------


def test_correlation_analysis_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import correlation_analysis

    with pytest.raises((ValueError, TypeError)):
        correlation_analysis(input=None)


def test_correlation_analysis_raises_too_few_columns() -> None:
    from nodyra_nodes.statistical_analysis import correlation_analysis

    with pytest.raises(ValueError, match="2 numeric"):
        correlation_analysis(input=ROWS_NUMERIC, columns="value", method="pearson")


def test_correlation_analysis_returns_dataset_ref(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import correlation_analysis

    result = correlation_analysis(
        input=ROWS_NUMERIC, columns="value,value2", method="pearson"
    )
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "pearson"
    assert result["n_pairs"] == 1


def test_correlation_analysis_spearman(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import correlation_analysis

    result = correlation_analysis(
        input=ROWS_NUMERIC, columns="value,value2", method="spearman"
    )
    assert result["method"] == "spearman"


def test_correlation_analysis_long_format_structure(store_ctx) -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.datasets import materialize_dataset
    from nodyra_nodes.statistical_analysis import correlation_analysis

    result = correlation_analysis(
        input=ROWS_NUMERIC, columns="value,value2", method="pearson"
    )
    rows = materialize_dataset(result["dataset"])
    assert all("col1" in r and "col2" in r and "correlation" in r for r in rows)


# ---------------------------------------------------------------------------
# monte_carlo_simulate
# ---------------------------------------------------------------------------


def test_monte_carlo_simulate_raises_without_variables() -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    with pytest.raises(ValueError):
        monte_carlo_simulate(variables_json="", expression="x")


def test_monte_carlo_simulate_raises_without_expression() -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    variables = json.dumps([{"name": "x", "distribution": "normal", "loc": 0.0, "scale": 1.0}])
    with pytest.raises(ValueError):
        monte_carlo_simulate(variables_json=variables, expression="")


def test_monte_carlo_simulate_returns_dataset(store_ctx) -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    variables = json.dumps([
        {"name": "x", "distribution": "normal", "loc": 10.0, "scale": 1.0},
    ])
    result = monte_carlo_simulate(
        variables_json=variables, expression="x", n_iterations=100
    )
    assert is_dataset_ref(result["dataset"])
    assert result["n_iterations"] == 100
    assert "result_mean" in result
    assert "result_p5" in result
    assert "result_p95" in result


def test_monte_carlo_simulate_uniform_distribution(store_ctx) -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    variables = json.dumps([
        {"name": "a", "distribution": "uniform", "low": 0.0, "high": 1.0},
        {"name": "b", "distribution": "uniform", "low": 0.0, "high": 1.0},
    ])
    result = monte_carlo_simulate(
        variables_json=variables, expression="a + b", n_iterations=200, random_seed=0
    )
    assert 0.5 < result["result_mean"] < 1.5


def test_monte_carlo_simulate_unknown_distribution_raises() -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    variables = json.dumps([
        {"name": "x", "distribution": "bogus_dist", "loc": 0.0, "scale": 1.0}
    ])
    with pytest.raises(ValueError, match="distribution"):
        monte_carlo_simulate(variables_json=variables, expression="x", n_iterations=10)


def test_monte_carlo_simulate_blocks_sandbox_escape() -> None:
    """SA-1: a sandbox-escape expression must be rejected, not executed."""
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    variables = json.dumps([
        {"name": "x", "distribution": "normal", "loc": 0.0, "scale": 1.0}
    ])
    # Classic escape: reach object subclasses with no builtins needed.
    with pytest.raises(ValueError, match="Invalid expression"):
        monte_carlo_simulate(
            variables_json=variables,
            expression="x.__class__.__bases__[0].__subclasses__()",
            n_iterations=5,
        )


def test_monte_carlo_simulate_invalid_json_raises() -> None:
    from nodyra_nodes.statistical_analysis import monte_carlo_simulate

    with pytest.raises(ValueError):
        monte_carlo_simulate(variables_json="not json", expression="x", n_iterations=10)


def test_monte_carlo_simulate_rejects_unbounded_iterations() -> None:
    from nodyra_nodes.statistical_analysis import (
        MAX_MONTE_CARLO_ITERATIONS,
        monte_carlo_simulate,
    )

    variables = json.dumps([
        {"name": "x", "distribution": "normal", "loc": 0.0, "scale": 1.0}
    ])
    with pytest.raises(ValueError, match="n_iterations"):
        monte_carlo_simulate(
            variables_json=variables,
            expression="x",
            n_iterations=MAX_MONTE_CARLO_ITERATIONS + 1,
        )


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    with pytest.raises((ValueError, TypeError)):
        bootstrap_ci(input=None, column="value")


def test_bootstrap_ci_raises_without_column() -> None:
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    with pytest.raises(ValueError, match="column"):
        bootstrap_ci(input=ROWS_NUMERIC, column="")


def test_bootstrap_ci_mean_returns_interval() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    result = bootstrap_ci(
        input=ROWS_NUMERIC,
        column="value",
        statistic="mean",
        confidence_level=0.95,
        n_resamples=999,
    )
    assert result["ci_lower"] <= result["estimate"] <= result["ci_upper"]
    assert result["confidence_level"] == 0.95
    assert result["n"] == len(ROWS_NUMERIC)


def test_bootstrap_ci_median() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    result = bootstrap_ci(input=ROWS_NUMERIC, column="value", statistic="median")
    assert "estimate" in result
    assert "ci_lower" in result


def test_bootstrap_ci_invalid_statistic_raises() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    with pytest.raises(ValueError, match="statistic"):
        bootstrap_ci(input=ROWS_NUMERIC, column="value", statistic="bogus_stat")


def test_bootstrap_ci_rejects_unbounded_resamples() -> None:
    from nodyra_nodes.statistical_analysis import MAX_BOOTSTRAP_RESAMPLES, bootstrap_ci

    with pytest.raises(ValueError, match="n_resamples"):
        bootstrap_ci(
            input=ROWS_NUMERIC,
            column="value",
            n_resamples=MAX_BOOTSTRAP_RESAMPLES + 1,
        )


def test_bootstrap_ci_rejects_invalid_confidence_level() -> None:
    from nodyra_nodes.statistical_analysis import bootstrap_ci

    with pytest.raises(ValueError, match="confidence_level"):
        bootstrap_ci(input=ROWS_NUMERIC, column="value", confidence_level=1.0)


# ---------------------------------------------------------------------------
# regression_analysis
# ---------------------------------------------------------------------------


def test_regression_analysis_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import regression_analysis

    with pytest.raises((ValueError, TypeError)):
        regression_analysis(
            input=None, target_column="value", feature_columns="value2"
        )


def test_regression_analysis_raises_without_target() -> None:
    from nodyra_nodes.statistical_analysis import regression_analysis

    with pytest.raises(ValueError):
        regression_analysis(
            input=ROWS_NUMERIC, target_column="", feature_columns="value2"
        )


def test_regression_analysis_raises_without_features() -> None:
    from nodyra_nodes.statistical_analysis import regression_analysis

    with pytest.raises(ValueError):
        regression_analysis(
            input=ROWS_NUMERIC, target_column="value", feature_columns=""
        )


def test_regression_analysis_raises_missing_statsmodels() -> None:
    with patch.dict(
        sys.modules,
        {
            "statsmodels": None,
            "statsmodels.api": None,
            "statsmodels.formula.api": None,
        },
    ):
        from nodyra_nodes.statistical_analysis import regression_analysis

        with pytest.raises(RuntimeError, match="statsmodels"):
            regression_analysis(
                input=ROWS_NUMERIC,
                target_column="value",
                feature_columns="value2",
            )


def test_regression_analysis_ols_returns_dataset(store_ctx) -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.statistical_analysis import regression_analysis

    result = regression_analysis(
        input=ROWS_NUMERIC,
        target_column="value",
        feature_columns="value2",
        model_type="ols",
    )
    assert is_dataset_ref(result["dataset"])
    assert "r_squared" in result
    assert "n_obs" in result
    assert result["n_obs"] == len(ROWS_NUMERIC)


def test_regression_analysis_coefficient_structure(store_ctx) -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.datasets import materialize_dataset
    from nodyra_nodes.statistical_analysis import regression_analysis

    result = regression_analysis(
        input=ROWS_NUMERIC,
        target_column="value",
        feature_columns="value2",
        model_type="ols",
    )
    coef_rows = materialize_dataset(result["dataset"])
    assert all(
        "feature" in r and "coef" in r and "p_value" in r for r in coef_rows
    )
    feat_names = [r["feature"] for r in coef_rows]
    assert "const" in feat_names
    assert "value2" in feat_names


# ---------------------------------------------------------------------------
# optimization_solve
# ---------------------------------------------------------------------------


def test_optimization_solve_raises_without_variables() -> None:
    from nodyra_nodes.statistical_analysis import optimization_solve

    with pytest.raises(ValueError):
        optimization_solve(
            variables_json="",
            constraints_json="[]",
            objective_json='{"coefficients":{"x":1}}',
        )


def test_optimization_solve_raises_without_objective() -> None:
    from nodyra_nodes.statistical_analysis import optimization_solve

    with pytest.raises(ValueError):
        optimization_solve(
            variables_json='[{"name":"x","type":"continuous","lower":0}]',
            constraints_json="[]",
            objective_json="",
        )


def test_optimization_solve_raises_missing_pulp() -> None:
    with patch.dict(sys.modules, {"pulp": None}):
        from nodyra_nodes.statistical_analysis import optimization_solve

        with pytest.raises(RuntimeError, match="pulp"):
            optimization_solve(
                variables_json='[{"name":"x","type":"continuous","lower":0}]',
                constraints_json="[]",
                objective_json='{"coefficients":{"x":1}}',
                sense="maximize",
            )


def test_optimization_solve_simple_lp() -> None:
    pytest.importorskip("pulp")
    from nodyra_nodes.statistical_analysis import optimization_solve

    # maximize 3x + 2y subject to x + y <= 4, x >= 0, y >= 0
    result = optimization_solve(
        variables_json=json.dumps([
            {"name": "x", "type": "continuous", "lower": 0},
            {"name": "y", "type": "continuous", "lower": 0},
        ]),
        constraints_json=json.dumps([
            {"coefficients": {"x": 1, "y": 1}, "sense": "<=", "rhs": 4},
        ]),
        objective_json=json.dumps({"coefficients": {"x": 3, "y": 2}}),
        sense="maximize",
    )
    assert result["status"].upper() in ("OPTIMAL",)
    assert abs(result["objective"] - 12.0) < 0.1


def test_optimization_solve_invalid_constraint_sense() -> None:
    pytest.importorskip("pulp")
    from nodyra_nodes.statistical_analysis import optimization_solve

    with pytest.raises(ValueError, match="constraint sense"):
        optimization_solve(
            variables_json=json.dumps([{"name": "x", "type": "continuous", "lower": 0}]),
            constraints_json=json.dumps([
                {"coefficients": {"x": 1}, "sense": "!=", "rhs": 5},
            ]),
            objective_json=json.dumps({"coefficients": {"x": 1}}),
            sense="minimize",
        )


# ---------------------------------------------------------------------------
# time_series_decompose
# ---------------------------------------------------------------------------


def test_time_series_decompose_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import time_series_decompose

    with pytest.raises((ValueError, TypeError)):
        time_series_decompose(input=None, value_column="value", period=4)


def test_time_series_decompose_raises_too_short() -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.statistical_analysis import time_series_decompose

    short = [{"value": float(i)} for i in range(5)]
    with pytest.raises(ValueError, match="2 full seasonal"):
        time_series_decompose(input=short, value_column="value", period=4)


def test_time_series_decompose_raises_missing_statsmodels() -> None:
    with patch.dict(
        sys.modules,
        {
            "statsmodels": None,
            "statsmodels.tsa": None,
            "statsmodels.tsa.seasonal": None,
        },
    ):
        from nodyra_nodes.statistical_analysis import time_series_decompose

        with pytest.raises(RuntimeError, match="statsmodels"):
            time_series_decompose(input=ROWS_TS, value_column="value", period=4)


def test_time_series_decompose_returns_dataset(store_ctx) -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.statistical_analysis import time_series_decompose

    result = time_series_decompose(input=ROWS_TS, value_column="value", period=4)
    assert is_dataset_ref(result["dataset"])
    assert result["period"] == 4
    assert result["model"] == "additive"


def test_time_series_decompose_components_in_output(store_ctx) -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.datasets import materialize_dataset
    from nodyra_nodes.statistical_analysis import time_series_decompose

    result = time_series_decompose(input=ROWS_TS, value_column="value", period=4)
    rows = materialize_dataset(result["dataset"])
    assert len(rows) == len(ROWS_TS)
    assert all("trend" in r and "seasonal" in r and "residual" in r for r in rows)


# ---------------------------------------------------------------------------
# dimensionality_reduce
# ---------------------------------------------------------------------------


def test_dimensionality_reduce_raises_without_input() -> None:
    from nodyra_nodes.statistical_analysis import dimensionality_reduce

    with pytest.raises((ValueError, TypeError)):
        dimensionality_reduce(input=None, feature_columns="f1,f2,f3")


def test_dimensionality_reduce_raises_missing_sklearn() -> None:
    with patch.dict(
        sys.modules,
        {
            "sklearn": None,
            "sklearn.decomposition": None,
            "sklearn.manifold": None,
            "sklearn.preprocessing": None,
        },
    ):
        from nodyra_nodes.statistical_analysis import dimensionality_reduce

        with pytest.raises(RuntimeError, match="scikit-learn"):
            dimensionality_reduce(
                input=ROWS_FEATURES, feature_columns="f1,f2,f3"
            )


def test_dimensionality_reduce_pca_returns_dataset(store_ctx) -> None:
    pytest.importorskip("sklearn")
    from nodyra_nodes.statistical_analysis import dimensionality_reduce

    result = dimensionality_reduce(
        input=ROWS_FEATURES,
        feature_columns="f1,f2,f3",
        method="pca",
        n_components=2,
    )
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "pca"
    assert "explained_variance_ratio" in result


def test_dimensionality_reduce_pca_output_columns(store_ctx) -> None:
    pytest.importorskip("sklearn")
    from nodyra_nodes.datasets import materialize_dataset
    from nodyra_nodes.statistical_analysis import dimensionality_reduce

    result = dimensionality_reduce(
        input=ROWS_FEATURES,
        feature_columns="f1,f2,f3",
        method="pca",
        n_components=2,
    )
    rows = materialize_dataset(result["dataset"])
    assert len(rows) == len(ROWS_FEATURES)
    assert all("component_1" in r and "component_2" in r for r in rows)


def test_dimensionality_reduce_tsne(store_ctx) -> None:
    pytest.importorskip("sklearn")
    from nodyra_nodes.statistical_analysis import dimensionality_reduce

    result = dimensionality_reduce(
        input=ROWS_FEATURES,
        feature_columns="f1,f2,f3",
        method="tsne",
        n_components=2,
    )
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "tsne"
    assert "explained_variance_ratio" not in result


def test_dimensionality_reduce_invalid_method_raises(store_ctx) -> None:
    pytest.importorskip("sklearn")
    from nodyra_nodes.statistical_analysis import dimensionality_reduce

    with pytest.raises(ValueError, match="method"):
        dimensionality_reduce(
            input=ROWS_FEATURES,
            feature_columns="f1,f2,f3",
            method="umap",
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_statistical_analysis_nodes_registered() -> None:
    from nodyra.sdk import registry

    manifests = {m.id: m for m in registry.manifests()}
    expected = [
        "statistical_test",
        "distribution_fit",
        "correlation_analysis",
        "regression_analysis",
        "monte_carlo_simulate",
        "bootstrap_ci",
        "optimization_solve",
        "time_series_decompose",
        "dimensionality_reduce",
    ]
    for node_id in expected:
        assert node_id in manifests, f"{node_id} not in registry"


def test_statistical_analysis_nodes_have_requirements() -> None:
    from nodyra.sdk import registry

    manifests = {m.id: m for m in registry.manifests()}
    for node_id in [
        "statistical_test",
        "distribution_fit",
        "correlation_analysis",
        "regression_analysis",
        "monte_carlo_simulate",
        "bootstrap_ci",
        "optimization_solve",
        "time_series_decompose",
        "dimensionality_reduce",
    ]:
        m = manifests.get(node_id)
        assert m is not None
        assert m.requirements, f"{node_id} has no requirements declared"


def _weekly_series() -> list[dict]:
    """13 weeks of daily volume with a pronounced weekend dip."""
    import random

    rng = random.Random(7)
    weekly = [1.00, 1.06, 1.05, 1.03, 1.12, 0.72, 0.61]
    return [
        {"orders": round((200 + 0.9 * i) * weekly[i % 7] + rng.uniform(-6, 6), 1)}
        for i in range(91)
    ]


def test_seasonality_detect_reports_the_fundamental_not_a_harmonic(store_ctx) -> None:
    """Weekly data must report 7, even when 14 or 21 scores marginally higher.

    Autocorrelation peaks at every multiple of the true period, and noise
    routinely lets one of them win: this series scored 21 at 0.5532 against
    7 at 0.5454. Reporting 21 tells the reader to build a three-week baseline
    for a plainly weekly cycle.
    """
    from nodyra_nodes.statistical_analysis import seasonality_detect

    result = seasonality_detect(
        input=_weekly_series(), value_column="orders", min_period=2, max_period=30
    )

    assert result["main"]["period"] == 7


def test_seasonality_detect_keeps_a_genuinely_longer_period(store_ctx) -> None:
    """A period is only demoted to a divisor that scores about as well."""
    import math

    rows = [{"v": math.sin(2 * math.pi * i / 12)} for i in range(96)]

    from nodyra_nodes.statistical_analysis import seasonality_detect

    result = seasonality_detect(
        input=rows, value_column="v", min_period=2, max_period=40
    )

    # 12 is the true period. Lag 6 is perfectly anti-correlated (-1.0), which
    # is not a six-day season, and must not be reported as one.
    assert result["main"]["period"] == 12
    assert result["main"]["score"] > 0
