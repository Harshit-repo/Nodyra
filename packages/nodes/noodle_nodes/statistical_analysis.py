"""Statistical analysis and data science nodes for Noodle.

All scientific packages (scipy, statsmodels, numpy, pulp, scikit-learn) are
lazy-imported inside each function body. noodle_nodes.datasets helpers are
imported at module scope — they are Noodle core, always present.
"""

from __future__ import annotations

import json
import math
from typing import Any

from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes.datasets import materialize_dataset, records_to_dataset


def _rows_from_input(value: Any) -> list[dict[str, Any]]:
    if is_dataset_ref(value):
        return materialize_dataset(value)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("records"), list):
            return [row for row in value["records"] if isinstance(row, dict)]
        return [value]
    return []


# ---------------------------------------------------------------------------
# statistical_test
# ---------------------------------------------------------------------------


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

    def _get_groups(rows_: list, group_col: str, val_col: str) -> dict:
        groups: dict = {}
        for r in rows_:
            g = r.get(group_col)
            if g is not None and r.get(val_col) is not None:
                groups.setdefault(g, []).append(float(r[val_col]))
        return groups

    def _second_vals(rows_: list, col2: str) -> list:
        return [float(r[col2]) for r in rows_ if col2 in r and r[col2] is not None]

    stat: float | None = None
    pval: float | None = None

    if test == "t_test_1samp":
        r = _stats.ttest_1samp(col_values, popmean=0.0, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "t_test_ind":
        if group_column:
            groups = _get_groups(rows, group_column, column)
            if len(groups) != 2:
                raise ValueError(
                    f"t_test_ind requires exactly 2 groups in {group_column!r}; "
                    f"found {list(groups.keys())}"
                )
            g1, g2 = list(groups.values())
        elif second_column:
            g1, g2 = col_values, _second_vals(rows, second_column)
        else:
            raise ValueError("t_test_ind requires group_column or second_column")
        r = _stats.ttest_ind(g1, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "t_test_paired":
        if not second_column:
            raise ValueError("t_test_paired requires second_column")
        g2 = _second_vals(rows, second_column)
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
            g1, g2 = col_values, _second_vals(rows, second_column)
        else:
            raise ValueError("mann_whitney requires group_column or second_column")
        r = _stats.mannwhitneyu(g1, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "ks_2samp":
        if not second_column:
            raise ValueError("ks_2samp requires second_column")
        g2 = _second_vals(rows, second_column)
        r = _stats.ks_2samp(col_values, g2, alternative=alternative)
        stat, pval = float(r.statistic), float(r.pvalue)

    elif test == "shapiro_wilk":
        r = _stats.shapiro(col_values)
        stat, pval = float(r.statistic), float(r.pvalue)

    else:
        valid = (
            "t_test_1samp, t_test_ind, t_test_paired, chi_square, "
            "anova, mann_whitney, ks_2samp, shapiro_wilk"
        )
        raise ValueError(f"Unknown test: {test!r}. Valid: {valid}")

    reject = pval < alpha
    interp = (
        f"Reject the null hypothesis (p={pval:.4f} < α={alpha}). Statistically significant."
        if reject else
        f"Fail to reject the null hypothesis (p={pval:.4f} ≥ α={alpha}). Not statistically significant."
    )

    if test in ("t_test_1samp", "t_test_ind", "t_test_paired", "anova") and 3 <= n <= 5000:
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


# ---------------------------------------------------------------------------
# distribution_fit
# ---------------------------------------------------------------------------


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
        import numpy as _np
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
    arr = _np.array(data, dtype=float)

    dist_names = [d.strip() for d in distributions.split(",") if d.strip()]
    fitted: list[dict] = []

    for name in dist_names:
        if not hasattr(_stats, name):
            continue
        dist = getattr(_stats, name)
        try:
            params = dist.fit(arr)
            ks_stat, ks_p = _stats.kstest(arr, name, args=params)
            ll = float(dist.logpdf(arr, *params).sum())
            k = len(params)
            n = len(data)
            aic = 2.0 * k - 2.0 * ll
            bic = k * math.log(n) - 2.0 * ll
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


# ---------------------------------------------------------------------------
# correlation_analysis
# ---------------------------------------------------------------------------


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

    vecs: dict[str, list] = {}
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


# ---------------------------------------------------------------------------
# regression_analysis
# ---------------------------------------------------------------------------


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
    # Validate user inputs BEFORE importing the optional heavy dependency so a
    # missing/incorrect input yields a clear "X is required" error instead of an
    # unrelated "install statsmodels" message (TEST-1).
    if input is None:
        raise ValueError("input is required")
    if not target_column:
        raise ValueError("target_column is required")
    if not feature_columns:
        raise ValueError("feature_columns is required")

    try:
        import statsmodels.api as _sm
    except ImportError as exc:
        raise RuntimeError(
            "statsmodels is required. Add statsmodels to the workflow environment and rebuild it."
        ) from exc

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
        raise ValueError("model_type must be 'ols' or 'logit'")

    feat_names = ["const"] + feats
    ci = model.conf_int()
    coef_rows = []
    for i, name in enumerate(feat_names):
        coef_rows.append({
            "feature": name,
            "coef": float(model.params[i]),
            "std_err": float(model.bse[i]),
            "t_stat": float(model.tvalues[i]),
            "p_value": float(model.pvalues[i]),
            "ci_lower": float(ci[i][0]),
            "ci_upper": float(ci[i][1]),
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


# ---------------------------------------------------------------------------
# monte_carlo_simulate
# ---------------------------------------------------------------------------


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
                f"Unknown distribution {dist!r}. Valid: {sorted(_DISTRIBUTIONS)}"
            )
        samples[name] = _DISTRIBUTIONS[dist](vd, n_iterations)

    # SECURITY (SA-1): eval with empty __builtins__ does NOT sandbox — the
    # expression `().__class__.__bases__[0].__subclasses__()` needs no builtins
    # and reaches arbitrary classes (→ RCE). Validate the AST through the same
    # hardened validator the engine uses for `{{ }}` expressions (blocks dunder
    # access, exec/eval/open/etc and restricts to arithmetic), then compile ONCE
    # and reuse the code object across iterations (also a large perf win — the
    # old code re-parsed the string n_iterations times).
    import ast as _ast

    from noodle.expr import _SAFE_BUILTINS, _ExprValidator

    try:
        tree = _ast.parse(expression, mode="eval")
        _ExprValidator().visit(tree)
        code_obj = compile(tree, "<monte_carlo>", "eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(
            f"Invalid expression {expression!r}: {exc}"
        ) from exc
    safe_globals: dict = {"__builtins__": _SAFE_BUILTINS}
    try:
        result_arr = _np.array([
            eval(  # noqa: S307 - AST-validated above, restricted builtins
                code_obj,
                safe_globals,
                {k: float(v[i]) for k, v in samples.items()},  # type: ignore[index]
            )
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


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


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
        import numpy as _np
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

    _STAT_FNS = {
        "mean": _np.mean,
        "median": _np.median,
        "std": _np.std,
        "var": _np.var,
        "sum": _np.sum,
    }
    if statistic not in _STAT_FNS:
        raise ValueError(f"statistic must be one of {sorted(_STAT_FNS)}")

    stat_fn = _STAT_FNS[statistic]
    arr = _np.array(data, dtype=float)
    estimate = float(stat_fn(arr))

    boot = _stats.bootstrap(
        (arr,),
        stat_fn,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        method="percentile",
    )

    return {
        "statistic": statistic,
        "estimate": estimate,
        "ci_lower": float(boot.confidence_interval.low),
        "ci_upper": float(boot.confidence_interval.high),
        "confidence_level": confidence_level,
        "n_resamples": n_resamples,
        "n": len(data),
    }


# ---------------------------------------------------------------------------
# optimization_solve
# ---------------------------------------------------------------------------


@node(
    name="Optimization Solve",
    id="optimization_solve",
    category="Statistical Analysis",
    requirements=["pulp>=2.7"],
)
def optimization_solve(
    variables_json: str = "",
    constraints_json: str = "[]",
    objective_json: str = "",
    sense: str = "minimize",
    solver: str = "CBC",
    time_limit_seconds: int = 60,
) -> dict:
    """Solve a linear or integer program defined as JSON coefficients."""
    # Validate inputs before the optional import (TEST-1) — see regression_analysis.
    if not variables_json:
        raise ValueError("variables_json is required")
    if not objective_json:
        raise ValueError("objective_json is required")

    try:
        import pulp as _pulp
    except ImportError as exc:
        raise RuntimeError(
            "pulp is required. Add pulp to the workflow environment and rebuild it."
        ) from exc

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

    prob += _pulp.lpSum(
        coef * lp_vars[var] for var, coef in obj_def["coefficients"].items()
    )

    for cd in constraint_defs:
        expr = _pulp.lpSum(
            coef * lp_vars[var] for var, coef in cd["coefficients"].items()
        )
        s, rhs = cd["sense"], cd["rhs"]
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
        objective_val: float | None = float(_pulp.value(prob.objective))
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


# ---------------------------------------------------------------------------
# time_series_decompose
# ---------------------------------------------------------------------------


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
    """Decompose a time series into trend, seasonal, and residual components."""
    # Validate inputs before the optional import (TEST-1) — see regression_analysis.
    if input is None:
        raise ValueError("input is required")

    try:
        from statsmodels.tsa.seasonal import seasonal_decompose as _decompose
    except ImportError as exc:
        raise RuntimeError(
            "statsmodels is required. Add statsmodels to the workflow environment and rebuild it."
        ) from exc

    rows = materialize_dataset(input) if is_dataset_ref(input) else list(input)
    values = [
        float(r[value_column])
        for r in rows
        if value_column in r and r[value_column] is not None
    ]

    if len(values) < 2 * period:
        raise ValueError(
            f"Need at least 2 full seasonal periods ({2 * period} points) "
            f"for decomposition with period={period}; got {len(values)}"
        )

    import numpy as _np
    arr = _np.array(values, dtype=float)
    decomp = _decompose(arr, model=model, period=period, extrapolate_trend="freq")

    out_rows = []
    for i, obs in enumerate(decomp.observed):
        tr = decomp.trend[i]
        re = decomp.resid[i]
        out_rows.append({
            "index": i,
            "observed": float(obs),
            "trend": float(tr) if not _np.isnan(tr) else None,
            "seasonal": float(decomp.seasonal[i]),
            "residual": float(re) if not _np.isnan(re) else None,
        })

    return {
        "dataset": records_to_dataset(out_rows),
        "period": period,
        "model": model,
        "n_points": len(values),
    }


# ---------------------------------------------------------------------------
# dimensionality_reduce
# ---------------------------------------------------------------------------


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

    explained_variance: list[float] | None = None
    if method == "pca":
        n_comp = min(n_components, X_scaled.shape[0], X_scaled.shape[1])
        reducer = _PCA(n_components=n_comp, random_state=random_state)
        X_reduced = reducer.fit_transform(X_scaled)
        explained_variance = [float(v) for v in reducer.explained_variance_ratio_]
    elif method == "tsne":
        # scikit-learn requires perplexity < n_samples; the default (30) errors
        # on small datasets. Clamp to a valid range so t-SNE works regardless of
        # row count (TEST-1).
        perplexity = max(1.0, min(30.0, X_scaled.shape[0] - 1))
        reducer = _TSNE(
            n_components=n_components,
            random_state=random_state,
            perplexity=perplexity,
        )
        X_reduced = reducer.fit_transform(X_scaled)
    else:
        raise ValueError("method must be 'pca' or 'tsne'")

    out_rows = [
        {f"component_{j + 1}": float(X_reduced[i, j]) for j in range(X_reduced.shape[1])}
        for i in range(len(rows))
    ]

    summary: dict = {
        "method": method,
        "n_components": X_reduced.shape[1],
        "n_rows": len(rows),
    }
    if explained_variance is not None:
        summary["explained_variance_ratio"] = explained_variance

    return {"dataset": records_to_dataset(out_rows), **summary}


# ---------------------------------------------------------------------------
# seasonality_detect
# ---------------------------------------------------------------------------


@node(
    name="Seasonality Detect",
    id="seasonality_detect",
    category="Statistical Analysis",
    icon="activity",
    requirements=["numpy>=1.24"],
    output_kinds={"candidates": "dataset"},
    outputs=["main", "candidates"],
    params={
        "value_column": {"description": "Numeric time-series value column."},
        "min_period": {"description": "Minimum period/lag to evaluate."},
        "max_period": {"description": "Maximum period/lag to evaluate."},
        "top_n": {"description": "Number of candidate periods to return."},
    },
)
def seasonality_detect(
    input=None,
    value_column: str = "value",
    min_period: int = 2,
    max_period: int = 52,
    top_n: int = 5,
) -> dict:
    """Detect likely seasonal periods using autocorrelation scores."""
    if input is None:
        raise ValueError("input is required")
    if not value_column:
        raise ValueError("value_column is required")

    try:
        import numpy as _np
    except ImportError as exc:
        raise RuntimeError(
            "numpy is required. Add numpy to the workflow environment and rebuild it."
        ) from exc

    rows = _rows_from_input(input)
    values = [
        float(row[value_column])
        for row in rows
        if value_column in row and row[value_column] is not None
    ]
    if len(values) < 6:
        raise ValueError("seasonality_detect requires at least 6 numeric values")

    arr = _np.asarray(values, dtype=float)
    arr = arr - float(arr.mean())
    std = float(arr.std())
    if std == 0:
        raise ValueError("value series is constant; seasonality cannot be detected")

    low = max(1, int(min_period or 2))
    high = min(max(low, int(max_period or 52)), max(1, len(arr) // 2))
    candidates: list[dict[str, Any]] = []
    for period in range(low, high + 1):
        left = arr[:-period]
        right = arr[period:]
        if len(left) < 3:
            continue
        score = float(_np.corrcoef(left, right)[0, 1])
        if not _np.isnan(score):
            candidates.append({"period": period, "autocorrelation": score})

    candidates.sort(key=lambda row: abs(float(row["autocorrelation"])), reverse=True)
    selected = candidates[: max(1, int(top_n or 5))]
    best = selected[0] if selected else None
    return {
        "main": {
            "period": best["period"] if best else None,
            "score": best["autocorrelation"] if best else None,
            "n_points": len(values),
            "periods_tested": len(candidates),
        },
        "candidates": records_to_dataset(selected) if selected else None,
    }


# ---------------------------------------------------------------------------
# survival_analysis
# ---------------------------------------------------------------------------


@node(
    name="Survival Analysis",
    id="survival_analysis",
    category="Statistical Analysis",
    icon="activity",
    requirements=["lifelines>=0.27", "pandas>=2.0"],
    output_kinds={"survival_curve": "dataset"},
    outputs=["main", "survival_curve"],
    params={
        "duration_column": {"description": "Observed duration/time-to-event column."},
        "event_column": {"description": "Boolean/0-1 event observed column."},
        "group_column": {"description": "Optional grouping column for Kaplan-Meier curves."},
    },
)
def survival_analysis(
    input=None,
    duration_column: str = "duration",
    event_column: str = "event",
    group_column: str = "",
) -> dict:
    """Fit Kaplan-Meier survival curves, optionally by group."""
    if input is None:
        raise ValueError("input is required")
    if not duration_column or not event_column:
        raise ValueError("duration_column and event_column are required")

    try:
        import pandas as _pd
        from lifelines import KaplanMeierFitter as _KaplanMeierFitter
    except ImportError as exc:
        raise RuntimeError(
            "lifelines and pandas are required. Add them to the workflow "
            "environment and rebuild it."
        ) from exc

    rows = _rows_from_input(input)
    if not rows:
        raise ValueError("input must contain records")
    frame = _pd.DataFrame(rows)
    for column in (duration_column, event_column):
        if column not in frame.columns:
            raise ValueError(f"missing required column: {column}")

    if group_column and group_column in frame.columns:
        groups = [(str(name), group) for name, group in frame.groupby(group_column)]
    else:
        groups = [("all", frame)]

    curve_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for label, group in groups:
        kmf = _KaplanMeierFitter()
        kmf.fit(
            durations=group[duration_column].astype(float),
            event_observed=group[event_column].astype(bool),
            label=label,
        )
        median = kmf.median_survival_time_
        summaries.append(
            {
                "group": label,
                "n": int(len(group)),
                "events": int(group[event_column].astype(bool).sum()),
                "median_survival_time": None if _pd.isna(median) else float(median),
            }
        )
        survival_frame = kmf.survival_function_.reset_index()
        timeline_column = survival_frame.columns[0]
        estimate_column = survival_frame.columns[1]
        for _, row in survival_frame.iterrows():
            curve_rows.append(
                {
                    "group": label,
                    "timeline": float(row[timeline_column]),
                    "survival_probability": float(row[estimate_column]),
                }
            )

    return {
        "main": {
            "groups": summaries,
            "group_count": len(summaries),
            "n_rows": int(len(frame)),
        },
        "survival_curve": records_to_dataset(curve_rows) if curve_rows else None,
    }


# ---------------------------------------------------------------------------
# sensitivity_analysis
# ---------------------------------------------------------------------------


@node(
    name="Sensitivity Analysis",
    id="sensitivity_analysis",
    category="Statistical Analysis",
    icon="scales",
    requirements=["SALib>=1.4", "numpy>=1.24"],
    output_kinds={"indices": "dataset"},
    outputs=["main", "indices"],
    params={
        "problem_json": {
            "description": "SALib problem object with names and bounds.",
            "multiline": True,
        },
        "outputs_json": {
            "description": "Model output vector matching the generated samples.",
            "multiline": True,
        },
    },
)
def sensitivity_analysis(
    input=None,
    problem_json: str = "",
    outputs_json: str = "",
) -> dict:
    """Compute Sobol sensitivity indices from model outputs."""
    if isinstance(input, dict):
        problem_json = problem_json or json.dumps(input.get("problem") or {})
        outputs_json = outputs_json or json.dumps(input.get("outputs") or [])
    if not problem_json or not outputs_json:
        raise ValueError("problem_json and outputs_json are required")

    try:
        import numpy as _np
        from SALib.analyze import sobol as _sobol
    except ImportError as exc:
        raise RuntimeError(
            "SALib and numpy are required. Add SALib and numpy to the workflow "
            "environment and rebuild it."
        ) from exc

    try:
        problem = json.loads(problem_json)
        outputs = json.loads(outputs_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"problem_json and outputs_json must be valid JSON: {exc}") from exc
    if not isinstance(problem, dict) or not isinstance(outputs, list):
        raise ValueError("problem_json must be an object and outputs_json must be an array")
    names = list(problem.get("names") or [])
    if not names:
        raise ValueError("problem_json must include a non-empty names array")

    y = _np.asarray(outputs, dtype=float)
    result = _sobol.analyze(problem, y, print_to_console=False)
    rows = []
    for index, name in enumerate(names):
        rows.append(
            {
                "parameter": name,
                "s1": float(result["S1"][index]),
                "s1_conf": float(result["S1_conf"][index]),
                "st": float(result["ST"][index]),
                "st_conf": float(result["ST_conf"][index]),
            }
        )

    return {
        "main": {"parameter_count": len(names), "sample_count": int(len(y))},
        "indices": records_to_dataset(rows),
    }
