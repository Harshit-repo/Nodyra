# Statistical Analysis Nodes — Wave 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 9 statistical analysis and data science nodes in `statistical_analysis.py` that have no equivalent in n8n.

**Architecture:** All nodes live in `packages/nodes/noodle_nodes/statistical_analysis.py`, registered in `__init__.py`. Scientific packages (scipy, statsmodels, numpy, pulp, scikit-learn) are lazy-imported inside each function body. `noodle_nodes.datasets` helpers (`materialize_dataset`, `records_to_dataset`, `is_dataset_ref`) are imported at module scope — they are Noodle core, not optional. Tests use real scipy/numpy/sklearn where installed, `pytest.importorskip` for statsmodels and pulp.

**Tech Stack:** `scipy>=1.10`, `statsmodels>=0.14`, `numpy>=1.24`, `pulp>=2.7`, `scikit-learn>=1.3`

**Spec:** `docs/superpowers/specs/2026-06-05-python-powered-unique-nodes-design.md` (Category 3 — Statistical Analysis)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `packages/nodes/noodle_nodes/statistical_analysis.py` | All 9 nodes |
| Modify | `packages/nodes/noodle_nodes/__init__.py` | Import + register |
| Create | `packages/nodes/tests/test_statistical_analysis.py` | All tests |

**Node list:**
1. `statistical_test` ★ — scipy
2. `distribution_fit` — scipy
3. `correlation_analysis` — scipy
4. `regression_analysis` — statsmodels
5. `monte_carlo_simulate` — numpy
6. `bootstrap_ci` — scipy
7. `optimization_solve` ★ — pulp
8. `time_series_decompose` — statsmodels
9. `dimensionality_reduce` — scikit-learn

---

## Task 1: Module scaffold + import-safety test

**Files:**
- Create: `packages/nodes/noodle_nodes/statistical_analysis.py`
- Create: `packages/nodes/tests/test_statistical_analysis.py`
- Modify: `packages/nodes/noodle_nodes/__init__.py`

- [ ] **Step 1: Scaffold the module**

```python
# packages/nodes/noodle_nodes/statistical_analysis.py
"""Statistical analysis and data science nodes for Noodle.

All scientific packages (scipy, statsmodels, numpy, pulp, scikit-learn) are
lazy-imported inside each function body. noodle_nodes.datasets helpers are
imported at module scope — they are Noodle core, always present.
"""

from __future__ import annotations

import json
import math

from noodle.datasets import is_dataset_ref
from noodle_nodes.datasets import materialize_dataset, records_to_dataset
from noodle.sdk import node
```

- [ ] **Step 2: Write the import-safety test**

```python
# packages/nodes/tests/test_statistical_analysis.py
"""Tests for statistical analysis nodes."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401 — registers nodes
from noodle.datasets import is_dataset_ref

_MODULES_AT_IMPORT = frozenset(sys.modules)

ROWS_NUMERIC = [
    {"value": 2.1, "group": "A", "value2": 2.3},
    {"value": 3.4, "group": "A", "value2": 3.1},
    {"value": 2.8, "group": "A", "value2": 2.9},
    {"value": 5.1, "group": "B", "value2": 5.0},
    {"value": 6.2, "group": "B", "value2": 6.3},
    {"value": 5.8, "group": "B", "value2": 5.7},
]


def test_statistical_analysis_importable_without_optional_packages() -> None:
    import noodle_nodes.statistical_analysis as mod
    for node_id in [
        "statistical_test", "distribution_fit", "correlation_analysis",
        "regression_analysis", "monte_carlo_simulate", "bootstrap_ci",
        "optimization_solve", "time_series_decompose", "dimensionality_reduce",
    ]:
        assert hasattr(mod, node_id), f"missing {node_id}"


def test_import_does_not_import_optional_packages() -> None:
    forbidden = {"scipy", "statsmodels", "pulp", "sklearn"}
    leaked = forbidden & _MODULES_AT_IMPORT
    assert not leaked, f"Optional packages leaked into module scope: {leaked}"
```

- [ ] **Step 3: Add to `__init__.py`**

After the `browser_automation` import line, add:
```python
from noodle_nodes import statistical_analysis as statistical_analysis
```
And add `"statistical_analysis"` to `__all__`.

- [ ] **Step 4: Run scaffold test to confirm it fails as expected**

```powershell
cd D:\noodle\packages\nodes; uv run pytest tests/test_statistical_analysis.py::test_statistical_analysis_importable_without_optional_packages -v
```
Expected: FAIL (no node functions yet)

---

## Task 2: `statistical_test` node

- [ ] **Step 1: Write tests**

```python
def test_statistical_test_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import statistical_test
    with pytest.raises((ValueError, TypeError)):
        statistical_test(input=None, test="t_test_1samp", column="value")


def test_statistical_test_raises_without_column() -> None:
    from noodle_nodes.statistical_analysis import statistical_test
    with pytest.raises(ValueError, match="column"):
        statistical_test(input=ROWS_NUMERIC, test="t_test_1samp", column="")


def test_statistical_test_ttest_1samp() -> None:
    scipy = pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    result = statistical_test(input=ROWS_NUMERIC, test="t_test_1samp", column="value", alpha=0.05)
    assert "statistic" in result
    assert "p_value" in result
    assert "reject_null" in result
    assert isinstance(result["reject_null"], bool)
    assert "interpretation" in result


def test_statistical_test_ttest_ind_with_group() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    result = statistical_test(
        input=ROWS_NUMERIC, test="t_test_ind", column="value",
        group_column="group", alpha=0.05
    )
    assert result["reject_null"] is True  # groups A and B are clearly different


def test_statistical_test_anova() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    result = statistical_test(
        input=ROWS_NUMERIC, test="anova", column="value",
        group_column="group", alpha=0.05
    )
    assert result["statistic"] > 0
    assert "interpretation" in result


def test_statistical_test_mann_whitney() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    result = statistical_test(
        input=ROWS_NUMERIC, test="mann_whitney", column="value",
        group_column="group", alpha=0.05
    )
    assert "p_value" in result


def test_statistical_test_shapiro_wilk() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    result = statistical_test(input=ROWS_NUMERIC, test="shapiro_wilk", column="value")
    assert "statistic" in result
    assert "p_value" in result


def test_statistical_test_small_sample_warning() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    small = [{"value": float(i)} for i in range(5)]
    result = statistical_test(input=small, test="t_test_1samp", column="value")
    assert "warnings" in result
    assert any("Small sample" in w for w in result["warnings"])


def test_statistical_test_unknown_test_raises() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import statistical_test
    with pytest.raises(ValueError, match="Unknown test"):
        statistical_test(input=ROWS_NUMERIC, test="bogus", column="value")


def test_statistical_test_raises_missing_scipy() -> None:
    with patch.dict(sys.modules, {"scipy": None, "scipy.stats": None}):
        import importlib
        import noodle_nodes.statistical_analysis as _mod
        # Re-import to get a fresh reference bypassing module cache
        from noodle_nodes.statistical_analysis import statistical_test
        with pytest.raises(RuntimeError, match="scipy"):
            # Force the import to fail by removing scipy from sys.modules
            pass  # tested by the missing_package test pattern below
```

Actually the missing-package test for scipy is tricky since scipy is installed. Let me use a different approach:

```python
def test_statistical_test_raises_missing_scipy() -> None:
    with patch.dict(sys.modules, {"scipy": None, "scipy.stats": None}):
        from noodle_nodes.statistical_analysis import statistical_test
        with pytest.raises((RuntimeError, ImportError)):
            statistical_test(input=ROWS_NUMERIC, test="t_test_1samp", column="value")
```

- [ ] **Step 2: Implement `statistical_test`**

```python
@node(
    name="Statistical Test",
    id="statistical_test",
    category="Statistical Analysis",
    requirements=["scipy>=1.10"],
)
def statistical_test(
    input=None,
    test: str = "t_test_ind",
    column: str = "",
    group_column: str = "",
    second_column: str = "",
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> dict:
    """Run a statistical hypothesis test on a DatasetRef or list of records."""
    try:
        from scipy import stats as _stats
    except ImportError as exc:
        raise RuntimeError(
            "scipy is required. Add scipy to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")
    if not column:
        raise ValueError("column is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    col_values = [float(r[column]) for r in rows if column in r and r[column] is not None]
    n = len(col_values)

    warnings_list: list[str] = []
    if n < 30:
        warnings_list.append(f"Small sample size (n={n}); p-value may be unreliable.")

    def _get_groups(rows, group_col, val_col):
        groups: dict = {}
        for r in rows:
            g = r.get(group_col)
            if g is not None and r.get(val_col) is not None:
                groups.setdefault(g, []).append(float(r[val_col]))
        return groups

    def _second_col_values(rows, col2):
        return [float(r[col2]) for r in rows if col2 in r and r[col2] is not None]

    stat, pval = None, None

    if test == "t_test_1samp":
        r = _stats.ttest_1samp(col_values, popmean=0.0, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "t_test_ind":
        if group_column:
            groups = _get_groups(rows, group_column, column)
            if len(groups) != 2:
                raise ValueError(
                    f"t_test_ind requires exactly 2 groups in {group_column!r}; found {list(groups.keys())}"
                )
            g1, g2 = list(groups.values())
        elif second_column:
            g1, g2 = col_values, _second_col_values(rows, second_column)
        else:
            raise ValueError("t_test_ind requires group_column or second_column")
        r = _stats.ttest_ind(g1, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "t_test_paired":
        if not second_column:
            raise ValueError("t_test_paired requires second_column")
        g2 = _second_col_values(rows, second_column)
        n2 = min(len(col_values), len(g2))
        r = _stats.ttest_rel(col_values[:n2], g2[:n2], alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "chi_square":
        freq_map: dict = {}
        for row in rows:
            v = row.get(column)
            if v is not None:
                freq_map[v] = freq_map.get(v, 0) + 1
        r = _stats.chisquare(list(freq_map.values()))
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "anova":
        if not group_column:
            raise ValueError("anova requires group_column")
        groups = _get_groups(rows, group_column, column)
        if len(groups) < 2:
            raise ValueError("anova requires at least 2 groups")
        r = _stats.f_oneway(*groups.values())
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "mann_whitney":
        if group_column:
            groups = _get_groups(rows, group_column, column)
            if len(groups) != 2:
                raise ValueError(
                    f"mann_whitney requires exactly 2 groups; found {list(groups.keys())}"
                )
            g1, g2 = list(groups.values())
        elif second_column:
            g1, g2 = col_values, _second_col_values(rows, second_column)
        else:
            raise ValueError("mann_whitney requires group_column or second_column")
        r = _stats.mannwhitneyu(g1, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "ks_2samp":
        if not second_column:
            raise ValueError("ks_2samp requires second_column")
        g2 = _second_col_values(rows, second_column)
        r = _stats.ks_2samp(col_values, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "shapiro_wilk":
        r = _stats.shapiro(col_values)
        stat, pval = float(r.statistic), float(r.pvalue)

    else:
        valid = "t_test_1samp, t_test_ind, t_test_paired, chi_square, anova, mann_whitney, ks_2samp, shapiro_wilk"
        raise ValueError(f"Unknown test: {test!r}. Valid: {valid}")

    reject = pval < alpha
    interp = (
        f"Reject the null hypothesis (p={pval:.4f} < α={alpha}). Statistically significant."
        if reject else
        f"Fail to reject the null hypothesis (p={pval:.4f} ≥ α={alpha}). Not statistically significant."
    )

    # Normality pre-check for parametric tests
    if test in ("t_test_1samp", "t_test_ind", "t_test_paired", "anova") and n <= 5000 and n >= 3:
        sw = _stats.shapiro(col_values[:5000])
        if sw.pvalue < 0.05:
            warnings_list.append(
                "Data may not be normally distributed (Shapiro-Wilk p<0.05). "
                "Consider a non-parametric alternative."
            )

    result: dict = {
        "test": test,
        "statistic": stat,
        "p_value": pval,
        "reject_null": reject,
        "interpretation": interp,
        "alpha": alpha,
        "n": n,
    }
    if warnings_list:
        result["warnings"] = warnings_list
    return result
```

---

## Task 3: `distribution_fit` node

- [ ] **Step 1: Write tests**

```python
def test_distribution_fit_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import distribution_fit
    with pytest.raises((ValueError, TypeError)):
        distribution_fit(input=None, column="value")


def test_distribution_fit_raises_without_column() -> None:
    from noodle_nodes.statistical_analysis import distribution_fit
    with pytest.raises(ValueError, match="column"):
        distribution_fit(input=ROWS_NUMERIC, column="")


def test_distribution_fit_returns_dataset_ref() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import distribution_fit
    result = distribution_fit(
        input=ROWS_NUMERIC, column="value", distributions="norm,expon"
    )
    assert is_dataset_ref(result["dataset"])
    assert result["distributions_tested"] >= 1


def test_distribution_fit_skips_bad_distribution_name() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import distribution_fit
    result = distribution_fit(
        input=ROWS_NUMERIC, column="value", distributions="norm,totally_fake_dist"
    )
    assert result["distributions_tested"] >= 1


def test_distribution_fit_sorted_by_ks_statistic() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import distribution_fit
    from noodle_nodes.datasets import materialize_dataset
    result = distribution_fit(
        input=ROWS_NUMERIC, column="value", distributions="norm,expon,lognorm"
    )
    rows = materialize_dataset(result["dataset"])
    ks_vals = [r["ks_statistic"] for r in rows]
    assert ks_vals == sorted(ks_vals)
```

- [ ] **Step 2: Implement `distribution_fit`**

```python
@node(
    name="Distribution Fit",
    id="distribution_fit",
    category="Statistical Analysis",
    requirements=["scipy>=1.10"],
)
def distribution_fit(
    input=None,
    column: str = "",
    distributions: str = "norm,expon,lognorm,gamma",
) -> dict:
    """Fit named distributions to a data column; rank by KS statistic."""
    try:
        from scipy import stats as _stats
    except ImportError as exc:
        raise RuntimeError(
            "scipy is required. Add scipy to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")
    if not column:
        raise ValueError("column is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    data = [float(r[column]) for r in rows if column in r and r[column] is not None]

    dist_names = [d.strip() for d in distributions.split(",") if d.strip()]
    fitted: list[dict] = []

    for name in dist_names:
        if not hasattr(_stats, name):
            continue
        dist = getattr(_stats, name)
        try:
            import numpy as _np
            params = dist.fit(data)
            ks_stat, ks_p = _stats.kstest(data, name, args=params)
            ll = float(dist.logpdf(_np.array(data, dtype=float), *params).sum())
            k = len(params)
            n = len(data)
            aic = 2 * k - 2 * ll
            bic = k * math.log(n) - 2 * ll
            fitted.append({
                "distribution": name,
                "params": str(params),
                "ks_statistic": float(ks_stat),
                "ks_pvalue": float(ks_p),
                "log_likelihood": ll,
                "aic": aic,
                "bic": bic,
            })
        except Exception:
            continue

    fitted.sort(key=lambda x: x["ks_statistic"])
    return {
        "dataset": records_to_dataset(fitted) if fitted else None,
        "distributions_tested": len(fitted),
        "n": len(data),
        "best_fit": fitted[0]["distribution"] if fitted else None,
    }
```

---

## Task 4: `correlation_analysis` node

- [ ] **Step 1: Write tests**

```python
def test_correlation_analysis_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import correlation_analysis
    with pytest.raises((ValueError, TypeError)):
        correlation_analysis(input=None)


def test_correlation_analysis_returns_dataset_ref() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import correlation_analysis
    result = correlation_analysis(input=ROWS_NUMERIC, columns="value,value2", method="pearson")
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "pearson"


def test_correlation_analysis_spearman() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import correlation_analysis
    result = correlation_analysis(input=ROWS_NUMERIC, columns="value,value2", method="spearman")
    assert result["method"] == "spearman"


def test_correlation_analysis_long_format_records() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import correlation_analysis
    from noodle_nodes.datasets import materialize_dataset
    result = correlation_analysis(input=ROWS_NUMERIC, columns="value,value2", method="pearson")
    rows = materialize_dataset(result["dataset"])
    assert all("col1" in r and "col2" in r and "correlation" in r for r in rows)
```

- [ ] **Step 2: Implement `correlation_analysis`**

```python
@node(
    name="Correlation Analysis",
    id="correlation_analysis",
    category="Statistical Analysis",
    requirements=["scipy>=1.10"],
)
def correlation_analysis(
    input=None,
    columns: str = "",
    method: str = "pearson",
) -> dict:
    """Compute pairwise correlations between numeric columns."""
    try:
        from scipy import stats as _stats
    except ImportError as exc:
        raise RuntimeError(
            "scipy is required. Add scipy to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)

    if columns:
        col_names = [c.strip() for c in columns.split(",") if c.strip()]
    else:
        # Auto-detect numeric columns
        sample = rows[0] if rows else {}
        col_names = [k for k, v in sample.items() if isinstance(v, (int, float))]

    if len(col_names) < 2:
        raise ValueError("correlation_analysis requires at least 2 numeric columns")

    _corr_fns = {
        "pearson": _stats.pearsonr,
        "spearman": _stats.spearmanr,
        "kendall": _stats.kendalltau,
    }
    if method not in _corr_fns:
        raise ValueError(f"method must be one of {list(_corr_fns)}")
    corr_fn = _corr_fns[method]

    # Build column vectors
    vecs: dict[str, list[float]] = {}
    for col in col_names:
        vecs[col] = [float(r[col]) for r in rows if col in r and r[col] is not None]

    pairs: list[dict] = []
    for i, c1 in enumerate(col_names):
        for c2 in col_names[i + 1:]:
            v1, v2 = vecs[c1], vecs[c2]
            n2 = min(len(v1), len(v2))
            r_val, p_val = corr_fn(v1[:n2], v2[:n2])
            pairs.append({
                "col1": c1,
                "col2": c2,
                "correlation": float(r_val),
                "p_value": float(p_val),
                "n": n2,
            })

    return {
        "dataset": records_to_dataset(pairs),
        "method": method,
        "n_pairs": len(pairs),
        "n_rows": len(rows),
    }
```

---

## Task 5: `monte_carlo_simulate` node

- [ ] **Step 1: Write tests**

```python
def test_monte_carlo_simulate_raises_without_variables() -> None:
    from noodle_nodes.statistical_analysis import monte_carlo_simulate
    with pytest.raises((ValueError, TypeError)):
        monte_carlo_simulate(variables_json="", expression="x")


def test_monte_carlo_simulate_returns_dataset() -> None:
    from noodle_nodes.statistical_analysis import monte_carlo_simulate
    variables = json.dumps([
        {"name": "x", "distribution": "normal", "loc": 10.0, "scale": 1.0},
    ])
    result = monte_carlo_simulate(variables_json=variables, expression="x", n_iterations=100)
    assert is_dataset_ref(result["dataset"])
    assert result["n_iterations"] == 100
    assert "result_mean" in result


def test_monte_carlo_simulate_uniform_distribution() -> None:
    from noodle_nodes.statistical_analysis import monte_carlo_simulate
    variables = json.dumps([
        {"name": "a", "distribution": "uniform", "low": 0.0, "high": 1.0},
        {"name": "b", "distribution": "uniform", "low": 0.0, "high": 1.0},
    ])
    result = monte_carlo_simulate(variables_json=variables, expression="a + b", n_iterations=50)
    assert 0.5 < result["result_mean"] < 1.5


def test_monte_carlo_simulate_bad_expression_raises() -> None:
    from noodle_nodes.statistical_analysis import monte_carlo_simulate
    variables = json.dumps([{"name": "x", "distribution": "normal", "loc": 0.0, "scale": 1.0}])
    with pytest.raises((ValueError, RuntimeError, NameError, SyntaxError)):
        monte_carlo_simulate(variables_json=variables, expression="__import__('os')")


def test_monte_carlo_simulate_unknown_distribution_raises() -> None:
    from noodle_nodes.statistical_analysis import monte_carlo_simulate
    variables = json.dumps([{"name": "x", "distribution": "bogus_dist", "loc": 0.0, "scale": 1.0}])
    with pytest.raises(ValueError, match="distribution"):
        monte_carlo_simulate(variables_json=variables, expression="x", n_iterations=10)
```

- [ ] **Step 2: Implement `monte_carlo_simulate`**

Expression evaluation uses a restricted namespace (only the declared variable names) with `eval()`. The expression must contain only arithmetic, not arbitrary Python — enforced by the restricted namespace.

```python
@node(
    name="Monte Carlo Simulate",
    id="monte_carlo_simulate",
    category="Statistical Analysis",
    requirements=["numpy>=1.24"],
)
def monte_carlo_simulate(
    variables_json: str = "",
    expression: str = "",
    n_iterations: int = 1000,
    random_seed: int = 42,
) -> dict:
    """Run Monte Carlo simulation with configurable variable distributions."""
    try:
        import numpy as _np
    except ImportError as exc:
        raise RuntimeError(
            "numpy is required. Add numpy to the workflow environment and rebuild it."
        ) from exc

    if not variables_json:
        raise ValueError("variables_json is required")
    if not expression:
        raise ValueError("expression is required")

    try:
        var_defs = json.loads(variables_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"variables_json is not valid JSON: {exc}") from exc

    rng = _np.random.default_rng(random_seed)
    _DISTRIBUTIONS = {
        "normal": lambda d, n: rng.normal(d.get("loc", 0.0), d.get("scale", 1.0), n),
        "uniform": lambda d, n: rng.uniform(d.get("low", 0.0), d.get("high", 1.0), n),
        "lognormal": lambda d, n: rng.lognormal(d.get("mean", 0.0), d.get("sigma", 1.0), n),
        "triangular": lambda d, n: rng.triangular(d["left"], d["mode"], d["right"], n),
        "exponential": lambda d, n: rng.exponential(d.get("scale", 1.0), n),
        "beta": lambda d, n: rng.beta(d.get("a", 2.0), d.get("b", 2.0), n),
    }

    samples: dict[str, object] = {}
    for vd in var_defs:
        name = vd.get("name", "")
        dist = vd.get("distribution", "normal")
        if dist not in _DISTRIBUTIONS:
            raise ValueError(
                f"Unknown distribution {dist!r}. Valid: {list(_DISTRIBUTIONS)}"
            )
        samples[name] = _DISTRIBUTIONS[dist](vd, n_iterations)

    # Restricted eval: only declared variable names are in scope
    allowed_names = set(samples)
    _SAFE_BUILTINS: dict = {}
    try:
        result_arr = _np.array([
            eval(expression, {"__builtins__": _SAFE_BUILTINS}, {k: float(v[i]) for k, v in samples.items()})  # type: ignore[index]
            for i in range(n_iterations)
        ])
    except Exception as exc:
        raise ValueError(f"Failed to evaluate expression {expression!r}: {exc}") from exc

    output_rows = []
    for i in range(n_iterations):
        row = {k: float(v[i]) for k, v in samples.items()}  # type: ignore[index]
        row["result"] = float(result_arr[i])
        output_rows.append(row)

    return {
        "dataset": records_to_dataset(output_rows),
        "n_iterations": n_iterations,
        "result_mean": float(result_arr.mean()),
        "result_std": float(result_arr.std()),
        "result_p5": float(_np.percentile(result_arr, 5)),
        "result_p95": float(_np.percentile(result_arr, 95)),
    }
```

---

## Task 6: `bootstrap_ci` node

- [ ] **Step 1: Write tests**

```python
def test_bootstrap_ci_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import bootstrap_ci
    with pytest.raises((ValueError, TypeError)):
        bootstrap_ci(input=None, column="value")


def test_bootstrap_ci_raises_without_column() -> None:
    from noodle_nodes.statistical_analysis import bootstrap_ci
    with pytest.raises(ValueError, match="column"):
        bootstrap_ci(input=ROWS_NUMERIC, column="")


def test_bootstrap_ci_mean_returns_interval() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import bootstrap_ci
    result = bootstrap_ci(
        input=ROWS_NUMERIC, column="value", statistic="mean",
        confidence_level=0.95, n_resamples=999
    )
    assert result["ci_lower"] <= result["estimate"] <= result["ci_upper"]
    assert result["confidence_level"] == 0.95


def test_bootstrap_ci_median() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import bootstrap_ci
    result = bootstrap_ci(input=ROWS_NUMERIC, column="value", statistic="median")
    assert "estimate" in result


def test_bootstrap_ci_invalid_statistic_raises() -> None:
    pytest.importorskip("scipy")
    from noodle_nodes.statistical_analysis import bootstrap_ci
    with pytest.raises(ValueError, match="statistic"):
        bootstrap_ci(input=ROWS_NUMERIC, column="value", statistic="bogus")
```

- [ ] **Step 2: Implement `bootstrap_ci`**

```python
@node(
    name="Bootstrap Confidence Interval",
    id="bootstrap_ci",
    category="Statistical Analysis",
    requirements=["scipy>=1.10"],
)
def bootstrap_ci(
    input=None,
    column: str = "",
    statistic: str = "mean",
    confidence_level: float = 0.95,
    n_resamples: int = 9999,
) -> dict:
    """Bootstrap confidence interval for a column statistic."""
    try:
        from scipy import stats as _stats
        import numpy as _np
    except ImportError as exc:
        raise RuntimeError(
            "scipy is required. Add scipy to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")
    if not column:
        raise ValueError("column is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    data = [float(r[column]) for r in rows if column in r and r[column] is not None]

    _STAT_FNS = {
        "mean": _np.mean,
        "median": _np.median,
        "std": _np.std,
        "var": _np.var,
        "sum": _np.sum,
    }
    if statistic not in _STAT_FNS:
        raise ValueError(f"statistic must be one of {list(_STAT_FNS)}")

    stat_fn = _STAT_FNS[statistic]
    arr = _np.array(data)
    estimate = float(stat_fn(arr))

    boot_result = _stats.bootstrap(
        (arr,), stat_fn,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        method="percentile",
    )

    return {
        "statistic": statistic,
        "estimate": estimate,
        "ci_lower": float(boot_result.confidence_interval.low),
        "ci_upper": float(boot_result.confidence_interval.high),
        "confidence_level": confidence_level,
        "n_resamples": n_resamples,
        "n": len(data),
    }
```

---

## Task 7: `regression_analysis` node

- [ ] **Step 1: Write tests**

```python
def test_regression_analysis_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import regression_analysis
    with pytest.raises((ValueError, TypeError)):
        regression_analysis(input=None, target_column="value", feature_columns="value2")


def test_regression_analysis_raises_without_target() -> None:
    from noodle_nodes.statistical_analysis import regression_analysis
    with pytest.raises(ValueError):
        regression_analysis(input=ROWS_NUMERIC, target_column="", feature_columns="value2")


def test_regression_analysis_raises_missing_statsmodels() -> None:
    with patch.dict(sys.modules, {"statsmodels": None, "statsmodels.api": None,
                                   "statsmodels.formula.api": None}):
        from noodle_nodes.statistical_analysis import regression_analysis
        with pytest.raises(RuntimeError, match="statsmodels"):
            regression_analysis(
                input=ROWS_NUMERIC, target_column="value", feature_columns="value2"
            )


def test_regression_analysis_ols_returns_dataset() -> None:
    sm = pytest.importorskip("statsmodels")
    from noodle_nodes.statistical_analysis import regression_analysis
    result = regression_analysis(
        input=ROWS_NUMERIC, target_column="value", feature_columns="value2", model_type="ols"
    )
    assert is_dataset_ref(result["dataset"])
    assert "r_squared" in result
    assert "n_obs" in result


def test_regression_analysis_coefficient_structure() -> None:
    pytest.importorskip("statsmodels")
    from noodle_nodes.statistical_analysis import regression_analysis
    from noodle_nodes.datasets import materialize_dataset
    result = regression_analysis(
        input=ROWS_NUMERIC, target_column="value", feature_columns="value2", model_type="ols"
    )
    coef_rows = materialize_dataset(result["dataset"])
    assert all("feature" in r and "coef" in r and "p_value" in r for r in coef_rows)
```

- [ ] **Step 2: Implement `regression_analysis`**

```python
@node(
    name="Regression Analysis",
    id="regression_analysis",
    category="Statistical Analysis",
    requirements=["statsmodels>=0.14"],
)
def regression_analysis(
    input=None,
    target_column: str = "",
    feature_columns: str = "",
    model_type: str = "ols",
) -> dict:
    """OLS or logit regression with full coefficient table."""
    try:
        import statsmodels.api as _sm
    except ImportError as exc:
        raise RuntimeError(
            "statsmodels is required. Add statsmodels to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")
    if not target_column:
        raise ValueError("target_column is required")
    if not feature_columns:
        raise ValueError("feature_columns is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    feats = [c.strip() for c in feature_columns.split(",") if c.strip()]

    import numpy as _np
    y = _np.array([float(r[target_column]) for r in rows], dtype=float)
    X_raw = _np.array([[float(r[f]) for f in feats] for r in rows], dtype=float)
    X = _sm.add_constant(X_raw)

    if model_type == "ols":
        model = _sm.OLS(y, X).fit()
    elif model_type == "logit":
        model = _sm.Logit(y, X).fit(disp=False)
    else:
        raise ValueError(f"model_type must be 'ols' or 'logit'")

    feat_names = ["const"] + feats
    coef_rows = []
    for i, name in enumerate(feat_names):
        coef_rows.append({
            "feature": name,
            "coef": float(model.params[i]),
            "std_err": float(model.bse[i]),
            "t_stat": float(model.tvalues[i]),
            "p_value": float(model.pvalues[i]),
            "ci_lower": float(model.conf_int()[i][0]),
            "ci_upper": float(model.conf_int()[i][1]),
        })

    summary: dict = {"n_obs": int(model.nobs), "model_type": model_type}
    if model_type == "ols":
        summary["r_squared"] = float(model.rsquared)
        summary["adj_r_squared"] = float(model.rsquared_adj)
        summary["f_statistic"] = float(model.fvalue)
        summary["p_value_f"] = float(model.f_pvalue)
    summary["aic"] = float(model.aic)
    summary["bic"] = float(model.bic)

    return {"dataset": records_to_dataset(coef_rows), **summary}
```

---

## Task 8: `optimization_solve` node

- [ ] **Step 1: Write tests**

```python
def test_optimization_solve_raises_without_variables() -> None:
    from noodle_nodes.statistical_analysis import optimization_solve
    with pytest.raises((ValueError, TypeError)):
        optimization_solve(variables_json="", constraints_json="[]", objective_json="{}")


def test_optimization_solve_raises_missing_pulp() -> None:
    with patch.dict(sys.modules, {"pulp": None}):
        from noodle_nodes.statistical_analysis import optimization_solve
        with pytest.raises(RuntimeError, match="pulp"):
            optimization_solve(
                variables_json='[{"name":"x","type":"continuous","lower":0}]',
                constraints_json='[{"coefficients":{"x":1},"sense":"<=","rhs":10}]',
                objective_json='{"coefficients":{"x":1}}',
                sense="maximize",
            )


def test_optimization_solve_simple_lp() -> None:
    pytest.importorskip("pulp")
    from noodle_nodes.statistical_analysis import optimization_solve
    # maximize 3x + 2y subject to x + y <= 4, x >= 0, y >= 0
    result = optimization_solve(
        variables_json='[{"name":"x","type":"continuous","lower":0},{"name":"y","type":"continuous","lower":0}]',
        constraints_json='[{"coefficients":{"x":1,"y":1},"sense":"<=","rhs":4}]',
        objective_json='{"coefficients":{"x":3,"y":2}}',
        sense="maximize",
    )
    assert result["status"] in ("Optimal", "OPTIMAL", "optimal")
    assert abs(result["objective"] - 12.0) < 0.1  # 3*4 + 2*0 = 12


def test_optimization_solve_infeasible() -> None:
    pytest.importorskip("pulp")
    from noodle_nodes.statistical_analysis import optimization_solve
    # x >= 10 and x <= 5 is infeasible
    result = optimization_solve(
        variables_json='[{"name":"x","type":"continuous","lower":10,"upper":5}]',
        constraints_json='[]',
        objective_json='{"coefficients":{"x":1}}',
        sense="minimize",
    )
    assert result["status"].upper() in ("INFEASIBLE", "UNDEFINED", "NOT SOLVED")
```

- [ ] **Step 2: Implement `optimization_solve`**

```python
@node(
    name="Optimization Solve",
    id="optimization_solve",
    category="Statistical Analysis",
    requirements=["pulp>=2.7"],
)
def optimization_solve(
    variables_json: str = "",
    constraints_json: str = "",
    objective_json: str = "",
    sense: str = "minimize",
    solver: str = "CBC",
    time_limit_seconds: int = 60,
) -> dict:
    """Solve a linear or integer program defined as JSON."""
    try:
        import pulp as _pulp
    except ImportError as exc:
        raise RuntimeError(
            "pulp is required. Add pulp to the workflow environment and rebuild it."
        ) from exc

    if not variables_json:
        raise ValueError("variables_json is required")
    if constraints_json == "":
        raise ValueError("constraints_json is required")
    if not objective_json:
        raise ValueError("objective_json is required")

    var_defs = json.loads(variables_json)
    constraint_defs = json.loads(constraints_json)
    obj_def = json.loads(objective_json)

    sense_val = _pulp.LpMinimize if sense.lower() == "minimize" else _pulp.LpMaximize
    prob = _pulp.LpProblem("noodle_opt", sense_val)

    lp_vars: dict = {}
    for vd in var_defs:
        name = vd["name"]
        cat = _pulp.LpInteger if vd.get("type") == "integer" else _pulp.LpContinuous
        lp_vars[name] = _pulp.LpVariable(
            name,
            lowBound=vd.get("lower"),
            upBound=vd.get("upper"),
            cat=cat,
        )

    prob += _pulp.lpSum(coef * lp_vars[var] for var, coef in obj_def["coefficients"].items())

    for cd in constraint_defs:
        expr = _pulp.lpSum(coef * lp_vars[var] for var, coef in cd["coefficients"].items())
        s = cd["sense"]
        rhs = cd["rhs"]
        if s == "<=":
            prob += expr <= rhs
        elif s == ">=":
            prob += expr >= rhs
        elif s == "==":
            prob += expr == rhs
        else:
            raise ValueError(f"Unknown constraint sense {s!r}. Use <=, >=, or ==")

    solver_obj = _pulp.getSolver(solver, timeLimit=time_limit_seconds, msg=False)
    prob.solve(solver_obj)

    status_str = _pulp.LpStatus[prob.status]
    if prob.status == 1:  # Optimal
        objective_val = float(_pulp.value(prob.objective))
        solution = {name: float(_pulp.value(v)) for name, v in lp_vars.items()}
    else:
        objective_val = None
        solution = {}

    return {
        "status": status_str,
        "objective": objective_val,
        "variables": solution,
        "sense": sense,
    }
```

---

## Task 9: `time_series_decompose` node

- [ ] **Step 1: Write tests**

```python
ROWS_TS = [
    {"date": f"2024-{(i % 12) + 1:02d}-01", "value": float(i) + (i % 4) * 2.0}
    for i in range(24)
]


def test_time_series_decompose_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import time_series_decompose
    with pytest.raises((ValueError, TypeError)):
        time_series_decompose(input=None, value_column="value", period=4)


def test_time_series_decompose_raises_missing_statsmodels() -> None:
    with patch.dict(sys.modules, {"statsmodels": None, "statsmodels.tsa": None,
                                   "statsmodels.tsa.seasonal": None}):
        from noodle_nodes.statistical_analysis import time_series_decompose
        with pytest.raises(RuntimeError, match="statsmodels"):
            time_series_decompose(input=ROWS_TS, value_column="value", period=4)


def test_time_series_decompose_returns_dataset() -> None:
    pytest.importorskip("statsmodels")
    from noodle_nodes.statistical_analysis import time_series_decompose
    result = time_series_decompose(input=ROWS_TS, value_column="value", period=4)
    assert is_dataset_ref(result["dataset"])
    assert result["period"] == 4
    assert result["model"] == "additive"


def test_time_series_decompose_components_in_output() -> None:
    pytest.importorskip("statsmodels")
    from noodle_nodes.statistical_analysis import time_series_decompose
    from noodle_nodes.datasets import materialize_dataset
    result = time_series_decompose(input=ROWS_TS, value_column="value", period=4)
    rows = materialize_dataset(result["dataset"])
    assert all("trend" in r and "seasonal" in r and "residual" in r for r in rows)
```

- [ ] **Step 2: Implement `time_series_decompose`**

```python
@node(
    name="Time Series Decompose",
    id="time_series_decompose",
    category="Statistical Analysis",
    requirements=["statsmodels>=0.14"],
)
def time_series_decompose(
    input=None,
    value_column: str = "value",
    period: int = 12,
    model: str = "additive",
) -> dict:
    """Decompose a time series into trend/seasonal/residual components."""
    try:
        from statsmodels.tsa.seasonal import seasonal_decompose as _decompose
    except ImportError as exc:
        raise RuntimeError(
            "statsmodels is required. Add statsmodels to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    values = [float(r[value_column]) for r in rows if value_column in r and r[value_column] is not None]

    if len(values) < 2 * period:
        raise ValueError(
            f"Need at least 2 full seasonal periods ({2 * period} points) "
            f"for decomposition with period={period}; got {len(values)}"
        )

    import numpy as _np
    arr = _np.array(values, dtype=float)
    result = _decompose(arr, model=model, period=period, extrapolate_trend="freq")

    output_rows = []
    for i, obs in enumerate(result.observed):
        output_rows.append({
            "index": i,
            "observed": float(obs),
            "trend": float(result.trend[i]) if not _np.isnan(result.trend[i]) else None,
            "seasonal": float(result.seasonal[i]),
            "residual": float(result.resid[i]) if not _np.isnan(result.resid[i]) else None,
        })

    return {
        "dataset": records_to_dataset(output_rows),
        "period": period,
        "model": model,
        "n_points": len(values),
    }
```

---

## Task 10: `dimensionality_reduce` node

- [ ] **Step 1: Write tests**

```python
ROWS_FEATURES = [
    {"f1": float(i), "f2": float(i * 2), "f3": float(i * 3)} for i in range(20)
]


def test_dimensionality_reduce_raises_without_input() -> None:
    from noodle_nodes.statistical_analysis import dimensionality_reduce
    with pytest.raises((ValueError, TypeError)):
        dimensionality_reduce(input=None, feature_columns="f1,f2,f3")


def test_dimensionality_reduce_raises_missing_sklearn() -> None:
    with patch.dict(sys.modules, {"sklearn": None, "sklearn.decomposition": None,
                                   "sklearn.manifold": None, "sklearn.preprocessing": None}):
        from noodle_nodes.statistical_analysis import dimensionality_reduce
        with pytest.raises(RuntimeError, match="scikit-learn"):
            dimensionality_reduce(input=ROWS_FEATURES, feature_columns="f1,f2,f3")


def test_dimensionality_reduce_pca_returns_dataset() -> None:
    pytest.importorskip("sklearn")
    from noodle_nodes.statistical_analysis import dimensionality_reduce
    result = dimensionality_reduce(
        input=ROWS_FEATURES, feature_columns="f1,f2,f3", method="pca", n_components=2
    )
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "pca"
    assert "explained_variance_ratio" in result


def test_dimensionality_reduce_pca_output_columns() -> None:
    pytest.importorskip("sklearn")
    from noodle_nodes.statistical_analysis import dimensionality_reduce
    from noodle_nodes.datasets import materialize_dataset
    result = dimensionality_reduce(
        input=ROWS_FEATURES, feature_columns="f1,f2,f3", method="pca", n_components=2
    )
    rows = materialize_dataset(result["dataset"])
    assert all("component_1" in r and "component_2" in r for r in rows)
    assert len(rows) == 20


def test_dimensionality_reduce_tsne() -> None:
    pytest.importorskip("sklearn")
    from noodle_nodes.statistical_analysis import dimensionality_reduce
    result = dimensionality_reduce(
        input=ROWS_FEATURES, feature_columns="f1,f2,f3", method="tsne", n_components=2
    )
    assert is_dataset_ref(result["dataset"])
    assert result["method"] == "tsne"
```

- [ ] **Step 2: Implement `dimensionality_reduce`**

```python
@node(
    name="Dimensionality Reduce",
    id="dimensionality_reduce",
    category="Statistical Analysis",
    requirements=["scikit-learn>=1.3"],
)
def dimensionality_reduce(
    input=None,
    feature_columns: str = "",
    method: str = "pca",
    n_components: int = 2,
    random_state: int = 42,
) -> dict:
    """Reduce dimensionality of feature columns using PCA or t-SNE."""
    try:
        from sklearn.decomposition import PCA as _PCA
        from sklearn.manifold import TSNE as _TSNE
        from sklearn.preprocessing import StandardScaler as _Scaler
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required. Add scikit-learn to the workflow environment and rebuild it."
        ) from exc

    if input is None:
        raise ValueError("input is required")

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)

    if feature_columns:
        feats = [c.strip() for c in feature_columns.split(",") if c.strip()]
    else:
        sample = rows[0] if rows else {}
        feats = [k for k, v in sample.items() if isinstance(v, (int, float))]

    import numpy as _np
    X = _np.array([[float(r.get(f, 0.0)) for f in feats] for r in rows], dtype=float)
    X_scaled = _Scaler().fit_transform(X)

    explained_variance = None
    if method == "pca":
        reducer = _PCA(n_components=min(n_components, X_scaled.shape[1]), random_state=random_state)
        X_reduced = reducer.fit_transform(X_scaled)
        explained_variance = [float(v) for v in reducer.explained_variance_ratio_]
    elif method == "tsne":
        reducer = _TSNE(n_components=n_components, random_state=random_state)
        X_reduced = reducer.fit_transform(X_scaled)
    else:
        raise ValueError(f"method must be 'pca' or 'tsne'")

    out_rows = []
    for i, row in enumerate(rows):
        out_row = {f"component_{j + 1}": float(X_reduced[i, j]) for j in range(X_reduced.shape[1])}
        out_rows.append(out_row)

    summary: dict = {"method": method, "n_components": X_reduced.shape[1], "n_rows": len(rows)}
    if explained_variance is not None:
        summary["explained_variance_ratio"] = explained_variance

    return {"dataset": records_to_dataset(out_rows), **summary}
```

---

## Task 11: Registration + full test run

- [ ] **Step 1: Write registration tests**

```python
def test_statistical_analysis_nodes_registered() -> None:
    from noodle.sdk import default_registry
    manifests = {m.id: m for m in default_registry.list()}
    expected = [
        "statistical_test", "distribution_fit", "correlation_analysis",
        "regression_analysis", "monte_carlo_simulate", "bootstrap_ci",
        "optimization_solve", "time_series_decompose", "dimensionality_reduce",
    ]
    for node_id in expected:
        assert node_id in manifests, f"{node_id} not in registry"


def test_statistical_analysis_nodes_have_requirements() -> None:
    from noodle.sdk import default_registry
    manifests = {m.id: m for m in default_registry.list()}
    for node_id in [
        "statistical_test", "distribution_fit", "correlation_analysis",
        "regression_analysis", "monte_carlo_simulate", "bootstrap_ci",
        "optimization_solve", "time_series_decompose", "dimensionality_reduce",
    ]:
        m = manifests.get(node_id)
        assert m is not None
        assert m.requirements, f"{node_id} has no requirements"
```

- [ ] **Step 2: Run full suite**

```powershell
cd D:\noodle\packages\nodes; uv run pytest tests/test_statistical_analysis.py -v
```

- [ ] **Step 3: Commit**

```bash
git add packages/nodes/noodle_nodes/statistical_analysis.py packages/nodes/noodle_nodes/__init__.py packages/nodes/tests/test_statistical_analysis.py docs/superpowers/plans/2026-06-07-python-nodes-wave3-statistical-analysis.md
git commit -m "feat(nodes): Wave 3 statistical analysis — 9 nodes (statistical_test, distribution_fit, correlation_analysis, regression_analysis, monte_carlo_simulate, bootstrap_ci, optimization_solve, time_series_decompose, dimensionality_reduce)"
```
