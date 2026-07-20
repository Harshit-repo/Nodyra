"""Lightweight Prometheus-compatible metrics (no external dependency).

Exposes a ``/metrics`` endpoint in OpenMetrics text format. Counters and
histograms are stored in-process; they reset on restart by design (a
short-lived process model). For persistent metrics, point Prometheus at
every replica and use ``honor_labels``.

Counters are safe for concurrent access: asyncio is single-threaded, so
``dict`` mutations (inc, set) are atomic between awaits.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.config import settings


def _escape_label_value(v: str) -> str:
    """Escape a Prometheus label value per the exposition format spec.

    Backslash-escapes ``\\``, ``"``, and ``\\n`` characters so arbitrary
    user-controlled strings (e.g. URL paths) cannot inject or corrupt the
    metric line.
    """
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# Regex that matches URL path segments that look like IDs / UUIDs / hex strings.
# Collapses them to a placeholder so label cardinality stays bounded.
_PATH_PARAM_RE = re.compile(
    r"/([0-9a-fA-F]{8,64}|[0-9a-fA-F-]{36}|[0-9]+)"
)


def _normalize_path(path: str) -> str:
    """Collapse dynamic path segments into ``/:param`` placeholders.

    Prevents unbounded label cardinality from URLs like
    ``/workflows/<uuid>/runs/<uuid>``.
    """
    return _PATH_PARAM_RE.sub("/:param", path)


# Cap on distinct label combinations per metric to prevent memory explosion.
# A metric exceeding this cap stops recording new combinations (existing ones
# keep updating).  2000 is generous for a single-process deployment.
_MAX_LABEL_COMBINATIONS = 2000


class _Counter:
    __slots__ = ("_name", "_help", "_labels", "_value")

    def __init__(self, name: str, help_text: str) -> None:
        self._name = name
        self._help = help_text
        self._value: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)

    def inc(self, amount: int = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        if len(self._value) < _MAX_LABEL_COMBINATIONS or key in self._value:
            self._value[key] += amount

    def render(self) -> str:
        lines: list[str] = []
        if self._help:
            lines.append(f"# HELP {self._name} {self._help}")
        lines.append(f"# TYPE {self._name} counter")
        for key, val in sorted(self._value.items()):
            if key:
                labels_str = ",".join(
                    f'{k}="{_escape_label_value(v)}"' for k, v in key
                )
                lines.append(f"{self._name}{{{labels_str}}} {val}")
            else:
                lines.append(f"{self._name} {val}")
        return "\n".join(lines)


class _Gauge:
    __slots__ = ("_name", "_help", "_labels", "_value")

    def __init__(self, name: str, help_text: str) -> None:
        self._name = name
        self._help = help_text
        self._value: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def _maybe_add_key(self, key: tuple) -> bool:
        if len(self._value) < _MAX_LABEL_COMBINATIONS or key in self._value:
            return True
        return False

    def set(self, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        if self._maybe_add_key(key):
            self._value[key] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        if self._maybe_add_key(key):
            self._value[key] += amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        if self._maybe_add_key(key):
            self._value[key] -= amount

    def render(self) -> str:
        lines: list[str] = []
        if self._help:
            lines.append(f"# HELP {self._name} {self._help}")
        lines.append(f"# TYPE {self._name} gauge")
        for key, val in sorted(self._value.items()):
            if key:
                labels_str = ",".join(
                    f'{k}="{_escape_label_value(v)}"' for k, v in key
                )
                lines.append(f"{self._name}{{{labels_str}}} {val}")
            else:
                lines.append(f"{self._name} {val}")
        return "\n".join(lines)


class _Histogram:
    __slots__ = ("_name", "_help", "_buckets", "_values", "_sum", "_count")

    def __init__(
        self, name: str, help_text: str,
        buckets: tuple[float, ...] = (0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0),
    ) -> None:
        self._name = name
        self._help = help_text
        self._buckets = buckets
        self._values: dict[tuple[tuple[str, str], ...], dict[float, int]] = (
            defaultdict(lambda: defaultdict(int))
        )
        self._sum: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._count: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        if (
            len(self._values) >= _MAX_LABEL_COMBINATIONS
            and key not in self._values
        ):
            return  # cardinality cap — skip new combinations
        self._sum[key] += value
        self._count[key] += 1
        vals = self._values[key]
        for bucket in self._buckets:
            if value <= bucket:
                vals[bucket] += 1

    def render(self) -> str:
        lines: list[str] = []
        if self._help:
            lines.append(f"# HELP {self._name} {self._help}")
        lines.append(f"# TYPE {self._name} histogram")
        for key in sorted(set(self._values.keys()) | set(self._sum.keys())):
            labels_str = (
                ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in key)
                if key else ""
            )
            label_suffix = f"{{{labels_str}}}" if labels_str else ""
            vals = self._values[key]
            for bucket in self._buckets:
                # observe() already stores cumulative counts (every bucket >=
                # the observed value is incremented), so emit directly —
                # re-summing here would double-count every observation.
                lines.append(
                    f"{self._name}_bucket{label_suffix}"
                    f"{{le=\"{bucket}\"}} {vals.get(bucket, 0)}"
                )
            # +Inf bucket
            lines.append(
                f"{self._name}_bucket{label_suffix}"
                f"{{le=\"+Inf\"}} {self._count[key]}"
            )
            lines.append(f"{self._name}_count{label_suffix} {self._count[key]}")
            lines.append(f"{self._name}_sum{label_suffix} {self._sum[key]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry — the metrics we expose
# ---------------------------------------------------------------------------

http_requests_total = _Counter(
    "nodyra_http_requests_total",
    "Total HTTP requests handled by the API.",
)

http_request_duration_seconds = _Histogram(
    "nodyra_http_request_duration_seconds",
    "HTTP request duration in seconds.",
)

run_starts_total = _Counter(
    "nodyra_run_starts_total",
    "Total workflow runs started.",
)

run_duration_seconds = _Histogram(
    "nodyra_run_duration_seconds",
    "Workflow run duration in seconds.",
    buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 300.0, 900.0, 3600.0),
)

active_runs = _Gauge(
    "nodyra_active_runs",
    "Workflow runs currently executing in this process.",
)

queue_depth = _Gauge(
    "nodyra_queue_depth",
    "Runs waiting in the durable queue.",
)

queue_leased = _Gauge(
    "nodyra_queue_leased",
    "Runs currently leased by this process.",
)

process_info = _Gauge(
    "nodyra_process_info",
    "Nodyra process presence grouped by execution-plane role.",
)
process_info.set(1, role=settings.dispatch_role)

node_executions_total = _Counter(
    "nodyra_node_executions_total",
    "Total node executions across all runs.",
)

output_store_events_total = _Counter(
    "nodyra_output_store_events_total",
    "Large-output offload store events, labeled by event=write_failed|read_failed. "
    "Offloading is best-effort (falls back to inline storage / the raw marker on "
    "failure), so these never fail a run — but a sustained rate means the output "
    "store is degraded and large outputs are silently bloating the DB or losing "
    "their resolved value.",
)

code_validation_blocked_total = _Counter(
    "nodyra_code_validation_blocked_total",
    "Code-node validation rejections, labeled by bounded reason and blocked target.",
)

registry_search_total = _Counter(
    "nodyra_registry_search_total",
    "Community registry search attempts grouped by outcome.",
)

registry_install_total = _Counter(
    "nodyra_registry_install_total",
    "Community registry installs grouped by bounded lifecycle status.",
)

template_instantiation_total = _Counter(
    "nodyra_template_instantiation_total",
    "Workflow template instantiations grouped by template and outcome.",
)

migration_preview_total = _Counter(
    "nodyra_migration_preview_total",
    "Workflow migration compatibility previews grouped by format and outcome.",
)

migration_import_total = _Counter(
    "nodyra_migration_import_total",
    "Workflow migration imports grouped by format and outcome.",
)


def _record_code_validation_blocked(reason: str, target: str) -> None:
    code_validation_blocked_total.inc(reason=reason, target=target)


def install_code_validation_hook() -> None:
    """Install (or restore) the API metrics hook on the core validator.

    The core setter is intentionally replaceable for embedders and tests. API
    startup calls this idempotently so an earlier temporary hook cannot leave
    production validation rejections unobserved.
    """

    from nodyra.expr import set_code_validation_blocked_hook

    set_code_validation_blocked_hook(_record_code_validation_blocked)


try:
    install_code_validation_hook()
except Exception:
    # Metrics are optional in non-API import contexts; never break startup.
    pass


def _render_all() -> str:
    metrics = [
        http_requests_total,
        http_request_duration_seconds,
        run_starts_total,
        run_duration_seconds,
        active_runs,
        queue_depth,
        queue_leased,
        process_info,
        node_executions_total,
        output_store_events_total,
        code_validation_blocked_total,
        registry_search_total,
        registry_install_total,
        template_instantiation_total,
        migration_preview_total,
        migration_import_total,
    ]
    return "\n\n".join(m.render() for m in metrics) + "\n"


def get_metrics_text() -> str:
    """Return the current metrics snapshot in Prometheus text format."""
    return _render_all()
