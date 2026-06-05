"""Machine Learning nodes — train, evaluate, and serve scikit-learn models.

These nodes are the ML plane of Noodle. They consume the dataset data plane
(``DatasetRef``/Parquet) and produce two new things:

* a **ModelRef** — a small JSON envelope wrapping a joblib-serialized
  scikit-learn ``Pipeline`` stored in the artifact store. It flows through
  node outputs, pins, and retry caches just like a DatasetRef.
* **metrics** — a plain JSON dict (accuracy/F1 for classification, R²/RMSE for
  regression) that downstream nodes can branch on or persist.

Heavy ML dependencies (scikit-learn, pandas, numpy, joblib) are imported
lazily inside each node so the base node library can be enumerated without
them. Run these nodes in an environment that has the ML packages installed
(see the "ML / Data Science" environment preset).
"""

from __future__ import annotations

import io
from typing import Any

from noodle.artifacts import read_bytes as artifact_read_bytes
from noodle.artifacts import write_bytes as artifact_write_bytes
from noodle.sdk import node
from noodle_nodes.datasets import dataframe_to_dataset, read_dataset

MODEL_MARKER = "__noodle_model__"
MODEL_VERSION = 1

_ML_IMPORT_ERROR = (
    "scikit-learn is required for ML nodes. Use an environment that installs "
    "scikit-learn, pandas, numpy, and joblib (the 'ML / Data Science' preset), "
    "then assign it to this workflow."
)


# ---------------------------------------------------------------------------
# Lazy imports + small helpers
# ---------------------------------------------------------------------------


def _sklearn():
    try:
        import joblib  # type: ignore[import-not-found]  # noqa: F401
        import numpy  # type: ignore[import-not-found]  # noqa: F401
        import pandas  # type: ignore[import-not-found]  # noqa: F401
        import sklearn  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(_ML_IMPORT_ERROR) from exc


def _num(value: Any) -> Any:
    """Coerce numpy scalars/arrays into JSON-safe Python values."""
    import numpy as np  # type: ignore[import-not-found]

    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_num(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_num(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _num(v) for k, v in value.items()}
    return value


def _to_dataframe(ref: Any):
    """Read a DatasetRef into a pandas DataFrame."""
    from noodle.datasets import is_dataset_ref

    if not is_dataset_ref(ref):
        raise ValueError(
            "expected a DatasetRef input — add a Records To Dataset or CSV "
            "Parse node upstream to produce a dataset."
        )
    conn, rel = read_dataset(ref)
    try:
        return rel.df()
    finally:
        conn.close()


def _coerce_columns(value: Any) -> list[str]:
    """Accept a list, comma-separated string, or blank for feature columns."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _resolve_features(df, target_column: str, feature_columns: Any) -> list[str]:
    requested = _coerce_columns(feature_columns)
    if requested:
        missing = [c for c in requested if c not in df.columns]
        if missing:
            raise ValueError(f"feature columns not found in dataset: {missing}")
        return requested
    return [c for c in df.columns if c != target_column]


def is_model_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(MODEL_MARKER) is True
        and value.get("version") == MODEL_VERSION
        and isinstance(value.get("artifact"), dict)
    )


def _make_model_ref(pipeline, *, task: str, algorithm: str,
                    features: list[str], target: str,
                    classes: list[Any] | None = None,
                    name: str = "model.joblib") -> dict[str, Any]:
    import joblib  # type: ignore[import-not-found]

    buf = io.BytesIO()
    joblib.dump(pipeline, buf)
    artifact = artifact_write_bytes(
        buf.getvalue(),
        name=name,
        content_type="application/octet-stream",
        kind="model",
        metadata={
            "task": task,
            "algorithm": algorithm,
            "features": features,
            "target": target,
        },
    )
    ref: dict[str, Any] = {
        MODEL_MARKER: True,
        "version": MODEL_VERSION,
        "artifact": artifact,
        "task": task,
        "algorithm": algorithm,
        "features": list(features),
        "target": target,
    }
    if classes is not None:
        ref["classes"] = _num(classes)
    return ref


def _load_pipeline(model_ref: Any):
    import joblib  # type: ignore[import-not-found]

    if not is_model_ref(model_ref):
        raise ValueError(
            "expected a trained model — wire the 'model' output of a Train "
            "Classifier or Train Regressor node into this input."
        )
    data = artifact_read_bytes(model_ref["artifact"])
    return joblib.load(io.BytesIO(data))


# ---------------------------------------------------------------------------
# Estimator factories
# ---------------------------------------------------------------------------


def _classifier(algorithm: str, random_state: int, max_iter: int):
    from sklearn.ensemble import (  # type: ignore[import-not-found]
        GradientBoostingClassifier,
        RandomForestClassifier,
    )
    from sklearn.linear_model import LogisticRegression  # type: ignore[import-not-found]
    from sklearn.neighbors import KNeighborsClassifier  # type: ignore[import-not-found]
    from sklearn.svm import SVC  # type: ignore[import-not-found]
    from sklearn.tree import DecisionTreeClassifier  # type: ignore[import-not-found]

    algo = (algorithm or "logistic_regression").lower()
    if algo == "logistic_regression":
        return LogisticRegression(max_iter=max_iter)
    if algo == "random_forest":
        return RandomForestClassifier(n_estimators=200, random_state=random_state)
    if algo == "gradient_boosting":
        return GradientBoostingClassifier(random_state=random_state)
    if algo == "decision_tree":
        return DecisionTreeClassifier(random_state=random_state)
    if algo == "svc":
        return SVC(probability=True, random_state=random_state)
    if algo == "knn":
        return KNeighborsClassifier()
    raise ValueError(f"unknown classification algorithm: {algorithm!r}")


def _regressor(algorithm: str, random_state: int):
    from sklearn.ensemble import (  # type: ignore[import-not-found]
        GradientBoostingRegressor,
        RandomForestRegressor,
    )
    from sklearn.linear_model import (  # type: ignore[import-not-found]
        Lasso,
        LinearRegression,
        Ridge,
    )
    from sklearn.neighbors import KNeighborsRegressor  # type: ignore[import-not-found]
    from sklearn.svm import SVR  # type: ignore[import-not-found]
    from sklearn.tree import DecisionTreeRegressor  # type: ignore[import-not-found]

    algo = (algorithm or "linear_regression").lower()
    if algo == "linear_regression":
        return LinearRegression()
    if algo == "ridge":
        return Ridge(random_state=random_state)
    if algo == "lasso":
        return Lasso(random_state=random_state)
    if algo == "random_forest":
        return RandomForestRegressor(n_estimators=200, random_state=random_state)
    if algo == "gradient_boosting":
        return GradientBoostingRegressor(random_state=random_state)
    if algo == "decision_tree":
        return DecisionTreeRegressor(random_state=random_state)
    if algo == "svr":
        return SVR()
    if algo == "knn":
        return KNeighborsRegressor()
    raise ValueError(f"unknown regression algorithm: {algorithm!r}")


def _build_pipeline(estimator, *, scale: bool):
    from sklearn.pipeline import Pipeline  # type: ignore[import-not-found]
    from sklearn.preprocessing import StandardScaler  # type: ignore[import-not-found]

    steps = []
    if scale:
        steps.append(("scaler", StandardScaler()))
    steps.append(("model", estimator))
    return Pipeline(steps)


def _feature_importances(pipeline, features: list[str]) -> list[dict[str, Any]] | None:
    model = pipeline.named_steps.get("model")
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        coef = getattr(model, "coef_", None)
        if coef is None:
            return None
        import numpy as np  # type: ignore[import-not-found]

        arr = np.asarray(coef)
        importances = np.abs(arr).mean(axis=0) if arr.ndim > 1 else np.abs(arr)
    pairs = [
        {"feature": f, "importance": _num(v)}
        for f, v in zip(features, importances, strict=False)
    ]
    pairs.sort(key=lambda p: p["importance"], reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

_CLASSIFIER_ALGOS = [
    "logistic_regression",
    "random_forest",
    "gradient_boosting",
    "decision_tree",
    "svc",
    "knn",
]
_REGRESSOR_ALGOS = [
    "linear_regression",
    "ridge",
    "lasso",
    "random_forest",
    "gradient_boosting",
    "decision_tree",
    "svr",
    "knn",
]


@node(
    name="Train Classifier",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="train_classifier",
    category="Machine Learning",
    icon="cpu",
    input_kinds={"input": "dataset"},
    outputs=["model", "metrics"],
    params={
        "target_column": {
            "description": "Column holding the class label to predict.",
        },
        "feature_columns": {
            "description": "Comma-separated feature columns. Blank = every "
            "column except the target.",
        },
        "algorithm": {"choices": _CLASSIFIER_ALGOS},
        "test_size": {
            "group": "Options",
            "description": "Fraction of rows held out to score the model "
            "(0.0–0.9).",
        },
        "scale": {"group": "Options", "description": "Standardize features before training."},
        "random_state": {"group": "Options", "description": "Seed for a reproducible split."},
        "max_iter": {"group": "Options", "description": "Max iterations for logistic regression."},
    },
)
def train_classifier(
    input: Any = None,
    target_column: str = "",
    feature_columns: Any = None,
    algorithm: str = "logistic_regression",
    test_size: float = 0.25,
    scale: bool = True,
    random_state: int = 42,
    max_iter: int = 1000,
) -> dict[str, Any]:
    """Train a classification model and score it on a held-out split.

    Outputs a downloadable ModelRef plus a metrics dict (accuracy, weighted
    precision/recall/F1, confusion matrix, class labels).
    """
    _sklearn()
    from sklearn.metrics import (  # type: ignore[import-not-found]
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import train_test_split  # type: ignore[import-not-found]

    if not target_column:
        raise ValueError("target_column is required for Train Classifier.")
    df = _to_dataframe(input)
    if target_column not in df.columns:
        raise ValueError(f"target column {target_column!r} not in dataset.")
    features = _resolve_features(df, target_column, feature_columns)
    if not features:
        raise ValueError("no feature columns available to train on.")

    frame = df[[*features, target_column]].dropna()
    if frame.empty:
        raise ValueError("no rows left after dropping missing values.")
    x = frame[features]
    y = frame[target_column]

    size = min(max(float(test_size or 0.0), 0.0), 0.9)
    stratify = y if size > 0 and y.nunique() > 1 else None
    if size > 0:
        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=size, random_state=random_state, stratify=stratify
        )
    else:
        x_train, x_test, y_train, y_test = x, x, y, y

    pipeline = _build_pipeline(
        _classifier(algorithm, random_state, max_iter), scale=scale
    )
    pipeline.fit(x_train, y_train)
    preds = pipeline.predict(x_test)

    classes = list(getattr(pipeline, "classes_", sorted(y.unique())))
    metrics = {
        "task": "classification",
        "algorithm": algorithm,
        "accuracy": _num(accuracy_score(y_test, preds)),
        "precision": _num(
            precision_score(y_test, preds, average="weighted", zero_division=0)
        ),
        "recall": _num(
            recall_score(y_test, preds, average="weighted", zero_division=0)
        ),
        "f1": _num(f1_score(y_test, preds, average="weighted", zero_division=0)),
        "classes": _num(classes),
        "confusion_matrix": _num(confusion_matrix(y_test, preds, labels=classes)),
        "n_train": int(len(x_train)),
        "n_test": int(len(x_test)),
        "features": features,
    }
    importances = _feature_importances(pipeline, features)
    if importances is not None:
        metrics["feature_importances"] = importances

    model_ref = _make_model_ref(
        pipeline,
        task="classification",
        algorithm=algorithm,
        features=features,
        target=target_column,
        classes=classes,
    )
    return {"model": model_ref, "metrics": metrics}


@node(
    name="Train Regressor",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="train_regressor",
    category="Machine Learning",
    icon="cpu",
    input_kinds={"input": "dataset"},
    outputs=["model", "metrics"],
    params={
        "target_column": {
            "description": "Numeric column to predict.",
        },
        "feature_columns": {
            "description": "Comma-separated feature columns. Blank = every "
            "column except the target.",
        },
        "algorithm": {"choices": _REGRESSOR_ALGOS},
        "test_size": {
            "group": "Options",
            "description": "Fraction of rows held out to score the model "
            "(0.0–0.9).",
        },
        "scale": {"group": "Options", "description": "Standardize features before training."},
        "random_state": {"group": "Options", "description": "Seed for a reproducible split."},
    },
)
def train_regressor(
    input: Any = None,
    target_column: str = "",
    feature_columns: Any = None,
    algorithm: str = "linear_regression",
    test_size: float = 0.25,
    scale: bool = True,
    random_state: int = 42,
) -> dict[str, Any]:
    """Train a regression model and score it on a held-out split.

    Outputs a downloadable ModelRef plus a metrics dict (R², MAE, MSE, RMSE,
    and linear coefficients when available).
    """
    _sklearn()
    import numpy as np  # type: ignore[import-not-found]
    from sklearn.metrics import (  # type: ignore[import-not-found]
        mean_absolute_error,
        mean_squared_error,
        r2_score,
    )
    from sklearn.model_selection import train_test_split  # type: ignore[import-not-found]

    if not target_column:
        raise ValueError("target_column is required for Train Regressor.")
    df = _to_dataframe(input)
    if target_column not in df.columns:
        raise ValueError(f"target column {target_column!r} not in dataset.")
    features = _resolve_features(df, target_column, feature_columns)
    if not features:
        raise ValueError("no feature columns available to train on.")

    frame = df[[*features, target_column]].dropna()
    if frame.empty:
        raise ValueError("no rows left after dropping missing values.")
    x = frame[features]
    y = frame[target_column]

    size = min(max(float(test_size or 0.0), 0.0), 0.9)
    if size > 0:
        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=size, random_state=random_state
        )
    else:
        x_train, x_test, y_train, y_test = x, x, y, y

    pipeline = _build_pipeline(_regressor(algorithm, random_state), scale=scale)
    pipeline.fit(x_train, y_train)
    preds = pipeline.predict(x_test)

    mse = float(mean_squared_error(y_test, preds))
    metrics = {
        "task": "regression",
        "algorithm": algorithm,
        "r2": _num(r2_score(y_test, preds)),
        "mae": _num(mean_absolute_error(y_test, preds)),
        "mse": _num(mse),
        "rmse": _num(np.sqrt(mse)),
        "n_train": int(len(x_train)),
        "n_test": int(len(x_test)),
        "features": features,
    }
    model = pipeline.named_steps.get("model")
    coef = getattr(model, "coef_", None)
    if coef is not None:
        metrics["coefficients"] = [
            {"feature": f, "coefficient": _num(c)}
            for f, c in zip(features, np.atleast_1d(coef), strict=False)
        ]
        intercept = getattr(model, "intercept_", None)
        if intercept is not None:
            metrics["intercept"] = _num(intercept)
    importances = _feature_importances(pipeline, features)
    if importances is not None:
        metrics["feature_importances"] = importances

    model_ref = _make_model_ref(
        pipeline,
        task="regression",
        algorithm=algorithm,
        features=features,
        target=target_column,
    )
    return {"model": model_ref, "metrics": metrics}


@node(
    name="Predict",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="ml_predict",
    category="Machine Learning",
    icon="sparkles",
    inputs=["model", "data"],
    input_kinds={"data": "dataset"},
    output_kinds={"main": "dataset"},
    params={
        "output_column": {
            "description": "Name of the column to write predictions into.",
        },
        "include_proba": {
            "description": "For classifiers, append per-class probability "
            "columns (proba_<class>).",
        },
    },
)
def ml_predict(
    model: Any = None,
    data: Any = None,
    output_column: str = "prediction",
    include_proba: bool = False,
) -> dict[str, Any]:
    """Apply a trained model to a dataset and append a prediction column.

    Returns a new DatasetRef with the original columns plus the predictions
    (and optional probability columns for classifiers).
    """
    _sklearn()
    pipeline = _load_pipeline(model)
    df = _to_dataframe(data)
    features = list(model.get("features") or [])
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing model features: {missing}")

    x = df[features] if features else df
    result = df.copy()
    result[output_column or "prediction"] = _num(list(pipeline.predict(x)))

    if include_proba and hasattr(pipeline, "predict_proba"):
        import numpy as np  # type: ignore[import-not-found]

        proba = np.asarray(pipeline.predict_proba(x))
        classes = list(getattr(pipeline, "classes_", model.get("classes") or []))
        for idx, cls in enumerate(classes):
            result[f"proba_{cls}"] = _num(list(proba[:, idx]))

    return dataframe_to_dataset(result, name="predictions.parquet")


@node(
    name="Evaluate Model",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="evaluate_model",
    category="Machine Learning",
    icon="gauge",
    inputs=["model", "data"],
    input_kinds={"data": "dataset"},
    params={
        "target_column": {
            "description": "Column with the true labels/values to score "
            "against. Blank = the model's training target.",
        },
    },
)
def evaluate_model(
    model: Any = None,
    data: Any = None,
    target_column: str = "",
) -> dict[str, Any]:
    """Score an existing model against a labelled dataset.

    Use this to evaluate a trained model on a fresh hold-out or production
    sample. Returns the same metric shape as the matching training node.
    """
    _sklearn()
    pipeline = _load_pipeline(model)
    task = str(model.get("task") or "")
    target = target_column or str(model.get("target") or "")
    if not target:
        raise ValueError("target_column is required to evaluate the model.")
    df = _to_dataframe(data)
    if target not in df.columns:
        raise ValueError(f"target column {target!r} not in dataset.")
    features = list(model.get("features") or [])
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing model features: {missing}")

    frame = df[[*features, target]].dropna()
    if frame.empty:
        raise ValueError("no rows left after dropping missing values.")
    x = frame[features]
    y = frame[target]
    preds = pipeline.predict(x)

    if task == "regression":
        import numpy as np  # type: ignore[import-not-found]
        from sklearn.metrics import (  # type: ignore[import-not-found]
            mean_absolute_error,
            mean_squared_error,
            r2_score,
        )

        mse = float(mean_squared_error(y, preds))
        return {
            "task": "regression",
            "r2": _num(r2_score(y, preds)),
            "mae": _num(mean_absolute_error(y, preds)),
            "mse": _num(mse),
            "rmse": _num(np.sqrt(mse)),
            "n_rows": int(len(frame)),
        }

    from sklearn.metrics import (  # type: ignore[import-not-found]
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )

    classes = list(getattr(pipeline, "classes_", sorted(y.unique())))
    return {
        "task": "classification",
        "accuracy": _num(accuracy_score(y, preds)),
        "precision": _num(
            precision_score(y, preds, average="weighted", zero_division=0)
        ),
        "recall": _num(recall_score(y, preds, average="weighted", zero_division=0)),
        "f1": _num(f1_score(y, preds, average="weighted", zero_division=0)),
        "classes": _num(classes),
        "confusion_matrix": _num(confusion_matrix(y, preds, labels=classes)),
        "n_rows": int(len(frame)),
    }


@node(
    name="Select Features",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="select_features",
    category="Machine Learning",
    icon="filter",
    input_kinds={"input": "dataset"},
    outputs=["main", "scores"],
    output_kinds={"main": "dataset"},
    params={
        "target_column": {"description": "Target column used to score features."},
        "k": {"description": "Number of top features to keep."},
        "task": {"group": "Options", "choices": ["auto", "classification", "regression"]},
    },
)
def select_features(
    input: Any = None,
    target_column: str = "",
    k: int = 5,
    task: str = "auto",
) -> dict[str, Any]:
    """Rank features by univariate strength and keep the top K.

    Outputs a reduced dataset (top-K features + target) and a ``scores`` list
    of ``{feature, score}`` sorted best-first.
    """
    _sklearn()
    import numpy as np  # type: ignore[import-not-found]
    from sklearn.feature_selection import (  # type: ignore[import-not-found]
        SelectKBest,
        f_classif,
        f_regression,
    )

    if not target_column:
        raise ValueError("target_column is required for Select Features.")
    df = _to_dataframe(input)
    if target_column not in df.columns:
        raise ValueError(f"target column {target_column!r} not in dataset.")

    features = [c for c in df.columns if c != target_column]
    numeric = df[features].select_dtypes(include=[np.number]).columns.tolist()
    if not numeric:
        raise ValueError("Select Features needs at least one numeric feature.")

    frame = df[[*numeric, target_column]].dropna()
    if frame.empty:
        raise ValueError("no rows left after dropping missing values.")
    x = frame[numeric]
    y = frame[target_column]

    resolved = task
    if resolved == "auto":
        resolved = (
            "classification"
            if (y.dtype == object or y.nunique() <= max(2, len(y) // 20))
            else "regression"
        )
    score_func = f_classif if resolved == "classification" else f_regression

    keep = max(1, min(int(k or 1), len(numeric)))
    selector = SelectKBest(score_func=score_func, k=keep)
    selector.fit(x, y)
    scores = [
        {"feature": f, "score": _num(s)}
        for f, s in zip(numeric, selector.scores_, strict=False)
    ]
    scores.sort(key=lambda p: (p["score"] is None, -(p["score"] or 0.0)))
    chosen = [s["feature"] for s in scores[:keep]]

    reduced = frame[[*chosen, target_column]]
    return {
        "main": dataframe_to_dataset(reduced, name="selected.parquet"),
        "scores": scores,
    }


@node(
    name="Save Model",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="save_model",
    category="Machine Learning",
    icon="save",
    inputs=["model"],
    params={
        "name": {
            "description": "Filename for the downloadable model artifact.",
        },
    },
)
def save_model(model: Any = None, name: str = "model.joblib") -> dict[str, Any]:
    """Persist a trained model as a named, downloadable run artifact.

    The model is re-written to the artifact store under ``name`` (so it shows
    up in the run's Artifacts panel) and the original ModelRef is passed
    through unchanged so downstream Predict/Evaluate nodes still work.
    """
    if not is_model_ref(model):
        raise ValueError(
            "Save Model expects a trained model on its 'model' input."
        )
    filename = (name or "model.joblib").strip() or "model.joblib"
    if not filename.lower().endswith((".joblib", ".pkl")):
        filename = f"{filename}.joblib"

    data = artifact_read_bytes(model["artifact"])
    saved = artifact_write_bytes(
        data,
        name=filename,
        content_type="application/octet-stream",
        kind="model",
        metadata={
            "task": model.get("task"),
            "algorithm": model.get("algorithm"),
            "target": model.get("target"),
        },
    )
    return {
        "model": model,
        "saved_as": filename,
        "artifact": saved,
        "size_bytes": len(data),
    }


# ---------------------------------------------------------------------------
# Persistent model registry (survives across runs/workflows)
# ---------------------------------------------------------------------------
#
# The registry lets one workflow train + register a model under a name and a
# *different* workflow (e.g. a webhook prediction service) load it by name.
# Models are stored on the shared artifact volume under a ``model-registry/``
# folder that lives *outside* ``runs/`` so run-retention never deletes them.


def _registry_root():
    """Return the persistent registry directory on the shared artifact volume."""
    from pathlib import Path

    from noodle.context import artifact_store

    store = artifact_store.get()
    if store is None:
        raise RuntimeError(
            "model registry is unavailable in this execution context."
        )
    root = Path(store.base_dir) / "model-registry"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _registry_slug(name: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").strip()).strip("-.")
    if not slug:
        raise ValueError("a non-empty model name is required.")
    return slug[:120]


def _registry_entry_dir(name: str):
    entry = _registry_root() / _registry_slug(name)
    entry.mkdir(parents=True, exist_ok=True)
    return entry


@node(
    name="Register Model",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="register_model",
    category="Machine Learning",
    icon="archive",
    inputs=["model"],
    params={
        "name": {
            "description": "Registry name to store the model under. Loading by "
            "this name returns the latest registered version.",
            "placeholder": "iris-species",
        },
    },
)
def register_model(model: Any = None, name: str = "") -> dict[str, Any]:
    """Save a trained model to the persistent registry under ``name``.

    Registered models survive across runs and workflows, so a separate
    prediction service (e.g. a webhook) can load them by name. The original
    ModelRef is passed through unchanged on the ``model`` output.
    """
    import json
    from datetime import UTC, datetime

    if not is_model_ref(model):
        raise ValueError(
            "Register Model expects a trained model on its 'model' input."
        )
    if not name or not name.strip():
        raise ValueError("a model name is required to register.")

    entry = _registry_entry_dir(name)
    data = artifact_read_bytes(model["artifact"])
    (entry / "model.joblib").write_bytes(data)
    meta = {
        "name": name.strip(),
        "slug": _registry_slug(name),
        "task": model.get("task"),
        "algorithm": model.get("algorithm"),
        "features": list(model.get("features") or []),
        "target": model.get("target"),
        "classes": model.get("classes"),
        "size_bytes": len(data),
        "registered_at": datetime.now(UTC).isoformat(),
    }
    (entry / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"model": model, "registered_as": meta["name"], "metadata": meta}


@node(
    name="Load Model",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="load_model",
    category="Machine Learning",
    icon="package",
    outputs=["model", "metadata"],
    params={
        "name": {
            "description": "Registry name of a previously registered model.",
            "placeholder": "iris-species",
        },
    },
)
def load_model(input: Any = None, name: str = "") -> dict[str, Any]:
    """Load a model from the persistent registry by name.

    Emits a ModelRef on ``model`` (wire it into Predict/Evaluate) plus the
    stored ``metadata``. Pair with a Webhook trigger to serve live
    predictions from a model trained in another workflow.
    """
    import json

    if not name or not name.strip():
        raise ValueError("a model name is required to load.")
    entry = _registry_root() / _registry_slug(name)
    model_path = entry / "model.joblib"
    meta_path = entry / "meta.json"
    if not model_path.exists() or not meta_path.exists():
        raise ValueError(
            f"no model named {name!r} in the registry. Register one first "
            f"with the Register Model node."
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    data = model_path.read_bytes()
    # Re-publish the bytes as an artifact in the *current* run so the standard
    # ModelRef → artifact-store read path used by Predict/Evaluate works.
    artifact = artifact_write_bytes(
        data,
        name=f"{meta.get('slug', 'model')}.joblib",
        content_type="application/octet-stream",
        kind="model",
        metadata={"loaded_from_registry": meta.get("name")},
    )
    model_ref: dict[str, Any] = {
        MODEL_MARKER: True,
        "version": MODEL_VERSION,
        "artifact": artifact,
        "task": meta.get("task"),
        "algorithm": meta.get("algorithm"),
        "features": list(meta.get("features") or []),
        "target": meta.get("target"),
    }
    if meta.get("classes") is not None:
        model_ref["classes"] = meta["classes"]
    return {"model": model_ref, "metadata": meta}


def _score_frame(pipeline, task: str, frame, features: list[str], target: str) -> dict[str, Any]:
    """Compute task-appropriate metrics for an already-cleaned frame."""
    x = frame[features]
    y = frame[target]
    preds = pipeline.predict(x)
    if task == "regression":
        import numpy as np  # type: ignore[import-not-found]
        from sklearn.metrics import (  # type: ignore[import-not-found]
            mean_absolute_error,
            mean_squared_error,
            r2_score,
        )

        mse = float(mean_squared_error(y, preds))
        return {
            "task": "regression",
            "r2": _num(r2_score(y, preds)),
            "mae": _num(mean_absolute_error(y, preds)),
            "mse": _num(mse),
            "rmse": _num(np.sqrt(mse)),
            "n_rows": int(len(frame)),
        }
    from sklearn.metrics import (  # type: ignore[import-not-found]
        accuracy_score,
        f1_score,
        precision_score,
        recall_score,
    )

    return {
        "task": "classification",
        "accuracy": _num(accuracy_score(y, preds)),
        "precision": _num(precision_score(y, preds, average="weighted", zero_division=0)),
        "recall": _num(recall_score(y, preds, average="weighted", zero_division=0)),
        "f1": _num(f1_score(y, preds, average="weighted", zero_division=0)),
        "n_rows": int(len(frame)),
    }


@node(
    name="Monitor Model",
    requirements=["scikit-learn", "joblib", "pandas"],
    id="monitor_model",
    category="Machine Learning",
    icon="activity",
    inputs=["model", "data"],
    input_kinds={"data": "dataset"},
    outputs=["metrics", "history", "alert"],
    params={
        "name": {
            "description": "Monitor key — metrics are appended to this model's "
            "history so you can track drift over time.",
            "placeholder": "iris-species",
        },
        "target_column": {
            "description": "Column with the true labels/values. Blank = the "
            "model's training target.",
        },
        "metric": {
            "group": "Options",
            "description": "Primary metric to watch.",
            "choices": ["auto", "accuracy", "f1", "r2", "rmse", "mae"],
        },
        "threshold": {
            "group": "Options",
            "description": "Raise an alert when the metric crosses this bound "
            "(below for higher-is-better metrics, above for error metrics). "
            "Blank disables the threshold check.",
        },
    },
)
def monitor_model(
    model: Any = None,
    data: Any = None,
    name: str = "",
    target_column: str = "",
    metric: str = "auto",
    threshold: Any = None,
) -> dict[str, Any]:
    """Score a model on fresh data and append the result to a drift history.

    Each call evaluates the model, records the metrics (with a timestamp) to
    the persistent registry, compares against the previous reading, and
    raises an ``alert`` when the watched metric degrades past ``threshold``.
    """
    import json
    from datetime import UTC, datetime

    _sklearn()
    pipeline = _load_pipeline(model)
    task = str(model.get("task") or "")
    target = target_column or str(model.get("target") or "")
    if not target:
        raise ValueError("target_column is required to monitor the model.")
    df = _to_dataframe(data)
    if target not in df.columns:
        raise ValueError(f"target column {target!r} not in dataset.")
    features = list(model.get("features") or [])
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing model features: {missing}")
    frame = df[[*features, target]].dropna()
    if frame.empty:
        raise ValueError("no rows left after dropping missing values.")

    metrics = _score_frame(pipeline, task or "classification", frame, features, target)
    metrics["timestamp"] = datetime.now(UTC).isoformat()

    watch = metric
    if watch == "auto":
        watch = "r2" if task == "regression" else "accuracy"
    current = metrics.get(watch)
    higher_is_better = watch not in ("rmse", "mae", "mse")

    # Append to the persistent monitor history (best-effort; skip if no name).
    history: list[dict[str, Any]] = []
    if name and name.strip():
        entry = _registry_entry_dir(name)
        log_path = entry / "monitor.jsonl"
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except ValueError:
                        pass
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics) + "\n")
    history = [*history, metrics]

    previous = history[-2] if len(history) > 1 else None
    delta = None
    if (
        previous is not None
        and isinstance(previous.get(watch), (int, float))
        and isinstance(current, (int, float))
    ):
        delta = _num(current - previous[watch])

    alert = False
    reasons: list[str] = []
    thr = None
    if threshold not in (None, ""):
        try:
            thr = float(threshold)
        except (TypeError, ValueError):
            thr = None
    if thr is not None and isinstance(current, (int, float)):
        if higher_is_better and current < thr:
            alert = True
            reasons.append(f"{watch}={_num(current)} below threshold {thr}")
        elif not higher_is_better and current > thr:
            alert = True
            reasons.append(f"{watch}={_num(current)} above threshold {thr}")

    return {
        "metrics": {
            **metrics,
            "metric": watch,
            "value": _num(current),
            "delta": delta,
            "alert": alert,
            "reasons": reasons,
        },
        "history": history,
        "alert": alert,
    }
