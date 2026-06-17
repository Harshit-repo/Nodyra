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
# HTTP-capable nodes with a visible URL param. These are only flagged when the
# configured URL points at a literal private/loopback target.
HTTP_NODE_TYPES: frozenset[str] = frozenset(
    {"http_request", "graphql_request", "ai_url_document_loader"}
)
# Nodes that read/write the runner host filesystem outside the artifact store.
# ``shapefile_read`` opens an arbitrary local ``path`` param (LFI) — same class
# as a Code node reading server files, so it goes under the deploy-time gate (R-3).
FILESYSTEM_NODE_TYPES: frozenset[str] = frozenset({"shapefile_read"})
# File reader nodes are only filesystem-risky when they use a server-side path.
# Browser-upload artifact reads stay inside the artifact store and are not flagged.
LOCAL_PATH_FILE_NODE_TYPES: frozenset[str] = frozenset(
    {
        "read_text_file",
        "read_csv_file",
        "read_json_file",
        "read_xml_file",
        "ai_file_document_loader",
    }
)
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
        "rss_feed_trigger",     # polls an arbitrary feed URL
        "model_endpoint_probe",      # probes caller-supplied model endpoint URL
        "model_endpoint_benchmark",  # load-tests caller-supplied model endpoint URL
        "shadow_compare_endpoint",   # probes two caller-supplied model endpoint URLs
        "ai_tool",                   # legacy model-invoked HTTP tool URL
        "ai_http_tool",              # model-invoked HTTP tool URL
        "ai_vector_retriever",       # credential carries caller-supplied Pinecone host
        "ai_qdrant_vector_store",    # credential carries caller-supplied Qdrant URL
        "mcp_tools",                 # connects to caller-supplied MCP server URL
        "mcp_call_tool",             # calls caller-supplied MCP server URL
        "mcp_list_tools",            # lists caller-supplied MCP server URL
        "mongodb_query",             # credentialed DB connection to arbitrary URI
        "redis_command",             # credentialed Redis connection to arbitrary URL
        "elasticsearch_search",      # HTTP search against arbitrary base URL
        "pinecone_upsert",           # credential carries caller-supplied index host
        "pinecone_query",            # credential carries caller-supplied index host
        "teams_send_webhook",        # credential is a caller-supplied webhook URL
        "calendly_get_event",        # fetches caller-supplied event URI
        "jira_create_issue",         # Jira site host is caller-supplied
        "shopify_list_orders",       # Shopify store domain is caller-supplied
        "git_clone",                 # clones arbitrary remote into runner filesystem
        "git_pull",                  # pulls arbitrary configured remote content
        "discord_send_message",      # legacy webhook URL is caller-supplied
        "smtp_send_email",           # legacy SMTP host is caller-supplied
        "postgres_query",            # legacy DB connection to arbitrary host/URI
        "mysql_query",               # legacy DB connection to arbitrary host
    }
)
# Legacy S3 nodes use AWS's fixed endpoint by default, but become arbitrary
# egress when endpoint_url is supplied for S3-compatible storage.
OPTIONAL_ENDPOINT_EGRESS_NODE_TYPES: frozenset[str] = frozenset(
    {"s3_put_object", "s3_get_object"}
)
AI_SUPPLIER_NODE_TYPES: frozenset[str] = frozenset(
    {"ai_chat_model_openai", "ai_chat_model_azure", "ai_embedding_model"}
)
CUSTOM_AI_PROVIDERS: frozenset[str] = frozenset({"openai_compatible", "ollama"})

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


def _file_change_uses_local_source(params: dict[str, Any]) -> bool:
    source_type = str(params.get("source_type") or "local").strip().lower()
    return source_type == "local" or _has_expression(source_type)


def _uses_local_path_param(params: dict[str, Any]) -> bool:
    path = params.get("path")
    return _has_expression(path) or (isinstance(path, str) and bool(path.strip()))


def _uses_endpoint_url(params: dict[str, Any]) -> bool:
    endpoint_url = params.get("endpoint_url")
    return _has_expression(endpoint_url) or (
        isinstance(endpoint_url, str) and bool(endpoint_url.strip())
    )


def _credential_params(params: dict[str, Any]) -> dict[str, Any]:
    credentials = params.get("credentials")
    return credentials if isinstance(credentials, dict) else {}


def _param_or_credential(params: dict[str, Any], key: str) -> Any:
    return params.get(key) or _credential_params(params).get(key)


def _uses_custom_ai_endpoint(nt: str, params: dict[str, Any]) -> bool:
    if nt == "ai_chat_model_azure":
        return True
    provider = str(_param_or_credential(params, "provider") or "").strip().lower()
    if provider in CUSTOM_AI_PROVIDERS:
        return True
    for key in ("base_url", "azure_endpoint"):
        value = _param_or_credential(params, key)
        if _has_expression(value) or (isinstance(value, str) and bool(value.strip())):
            return True
    return False


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

        if (
            nt in FILESYSTEM_NODE_TYPES
            or (nt == "file_change_trigger" and _file_change_uses_local_source(params))
            or (nt in LOCAL_PATH_FILE_NODE_TYPES and _uses_local_path_param(params))
        ):
            findings.append(
                {
                    "node_id": nid,
                    "type": nt,
                    "kind": "filesystem",
                    "reason": f"node type '{nt}' reads/writes the runner host filesystem",
                }
            )

        if (
            nt in NETWORK_EGRESS_NODE_TYPES
            or (nt in OPTIONAL_ENDPOINT_EGRESS_NODE_TYPES and _uses_endpoint_url(params))
            or (nt in AI_SUPPLIER_NODE_TYPES and _uses_custom_ai_endpoint(nt, params))
        ):
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
