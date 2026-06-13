"""Detect risky nodes in a workflow graph before production activation.

This module is consumed by the deployments router: when a deployment becomes
``active=True``, every finding is checked against ``UNSAFE_NODE_POLICY``:

- ``allow`` — findings are recorded but never block.
- ``warn`` — same as allow on the server, UI surfaces them.
- ``require_approval`` — activation is rejected unless the caller passes
  ``approve_unsafe_nodes=True``, which counts as explicit acknowledgement.
- ``block`` — activation is always rejected; no escape hatch in this mode.

Each finding is ``{node_id, type, kind, reason}``. ``kind`` is one of:
``code``, ``execute_command``, ``ssh``, ``filesystem``, ``http_private_ip``,
``sql_with_expressions``, ``network_egress``. The router treats every finding
equally (kind is informational/UI-only), so new kinds slot in without touching it.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

from noodle.models import WorkflowGraph

# Node type id → finding kind. Pure ``ssh``/``code``/``execute_command`` nodes
# are flagged unconditionally because they execute arbitrary code on the
# runner host.
UNCONDITIONAL_UNSAFE: dict[str, str] = {
    "code": "code",
    "execute_command": "execute_command",
    "ssh_execute": "ssh",
    # DSQ-2: these run author-supplied SQL / Python on the runner host. DuckDB
    # SQL can read arbitrary server files (``read_csv_auto('/etc/passwd')``) and
    # Polars code is arbitrary Python — same class as a Code node, so they go
    # under the same deploy-time policy gate. (duckdb_sql also has a runtime
    # ``enable_external_access=false`` latch as defence-in-depth.)
    "duckdb_sql": "code",
    "polars_transform": "code",
}

# Node type ids whose params get conditionally inspected.
SQL_NODE_TYPES: frozenset[str] = frozenset({"postgres_query", "mysql_query"})
HTTP_NODE_TYPES: frozenset[str] = frozenset({"http_request"})
# Nodes that read/write the runner host filesystem outside the artifact store.
# ``shapefile_read`` opens an arbitrary local ``path`` param (LFI) — same class
# as a Code node reading server files, so it goes under the deploy-time gate (R-3).
FILESYSTEM_NODE_TYPES: frozenset[str] = frozenset({"shapefile_read"})
# Nodes that open raw network connections to a caller-supplied host (SSRF /
# internal recon / credentialed egress). Unconditionally flagged because the
# target is arbitrary and not subject to the http_request private-IP check (R-3).
NETWORK_EGRESS_NODE_TYPES: frozenset[str] = frozenset(
    {
        "network_port_probe",   # TCP port scanner
        "sftp_transfer",        # credentialed file transfer to any host
        "ldap_query",           # credentialed directory query to any host
        "certificate_inspect",  # connects to any host:port when ``host`` set
        "sitemap_crawl",        # fetches an arbitrary URL
    }
)

_EXPR_PATTERN = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_HOST_HEADER_KEYS: frozenset[str] = frozenset({"host"})


def _graph_nodes(graph: dict | WorkflowGraph) -> list[Any]:
    if isinstance(graph, WorkflowGraph):
        return list(graph.nodes)
    return list((graph or {}).get("nodes") or [])


def _node_field(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        return node.get(key)
    return getattr(node, key, None)


def _params(node: Any) -> dict[str, Any]:
    raw = _node_field(node, "params")
    return raw if isinstance(raw, dict) else {}


def _has_expression(value: Any) -> bool:
    """True if ``value`` contains a templating expression that resolves at run time.

    Used to flag SQL queries that interpolate untrusted upstream values
    (the classic injection vector). String-only check — non-string params
    can't be expressions in the engine.
    """
    return isinstance(value, str) and bool(_EXPR_PATTERN.search(value))


def _is_private_host(host: str) -> bool:
    """True if ``host`` is a literal private/loopback/link-local IP or a
    well-known private hostname.

    DNS resolution is intentionally NOT performed (it would couple this
    check to runtime network state and let attackers race the check). The
    server-side block exists to catch obvious mistakes; a determined caller
    can always bypass with DNS, which is why the policy ladder also offers
    ``require_approval`` for full-program safety.
    """
    host = host.strip().lower()
    if not host:
        return False
    if host in {"localhost", "broadcasthost"}:
        return True
    # Strip surrounding brackets from IPv6 literals.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
    )


def _http_private_target(params: dict[str, Any]) -> str | None:
    """Return the offending URL/host when an HTTP node points at a private IP."""
    url = params.get("url")
    if isinstance(url, str) and url:
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        host = parsed.hostname or ""
        if _is_private_host(host):
            return url
    return None


def classify(graph: dict | WorkflowGraph) -> list[dict[str, str]]:
    """Return a list of findings, one per risky node.

    Deterministic order: nodes are walked in graph insertion order so the
    UI can render a stable list. Empty result means the graph is safe.
    """
    findings: list[dict[str, str]] = []
    for node in _graph_nodes(graph):
        nid = _node_field(node, "id") or ""
        nt = _node_field(node, "type") or ""
        if not isinstance(nid, str) or not isinstance(nt, str):
            continue

        if nt in UNCONDITIONAL_UNSAFE:
            findings.append(
                {
                    "node_id": nid,
                    "type": nt,
                    "kind": UNCONDITIONAL_UNSAFE[nt],
                    "reason": f"node type '{nt}' executes arbitrary code on the runner host",
                }
            )
            continue

        params = _params(node)

        if nt in HTTP_NODE_TYPES:
            offender = _http_private_target(params)
            if offender is not None:
                findings.append(
                    {
                        "node_id": nid,
                        "type": nt,
                        "kind": "http_private_ip",
                        "reason": f"HTTP target '{offender}' points at a private/loopback IP",
                    }
                )

        if nt in SQL_NODE_TYPES:
            query = params.get("query") or params.get("sql")
            if _has_expression(query):
                findings.append(
                    {
                        "node_id": nid,
                        "type": nt,
                        "kind": "sql_with_expressions",
                        "reason": (
                            "SQL query interpolates a templated expression — "
                            "potential injection vector"
                        ),
                    }
                )

        if nt in FILESYSTEM_NODE_TYPES:
            findings.append(
                {
                    "node_id": nid,
                    "type": nt,
                    "kind": "filesystem",
                    "reason": f"node type '{nt}' reads/writes the runner host filesystem",
                }
            )

        if nt in NETWORK_EGRESS_NODE_TYPES:
            findings.append(
                {
                    "node_id": nid,
                    "type": nt,
                    "kind": "network_egress",
                    "reason": (
                        f"node type '{nt}' opens a raw network connection to a "
                        "caller-supplied host (SSRF / internal recon)"
                    ),
                }
            )

    return findings


__all__ = ["classify"]
