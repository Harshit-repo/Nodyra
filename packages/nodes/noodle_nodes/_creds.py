"""Shared helper for declaring credential metadata on node params.

Use ``cred_single`` for a one-field credential (API key, bot token,
webhook URL, …) and ``cred_multi`` for multi-field credentials where the
node should receive the whole credential dict as one parameter (n8n-style
single "Credentials" picker).
"""

from __future__ import annotations

from typing import Any


def cred_single(
    credential_type: str,
    key: str,
    label: str,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Mark a string param as a single-field credential."""
    return {
        "credential": {
            "type": credential_type,
            "key": key,
            "label": label,
            "fields": fields or [key],
            "multi": False,
        }
    }


def cred_multi(
    credential_type: str,
    label: str,
    fields: list[str],
) -> dict[str, Any]:
    """Mark a dict param as a multi-field credential.

    Renders as a single "Credentials" picker in the inspector. The runtime
    resolves the reference to a dict of ``{field: value}`` for every named
    field — the node function receives that dict directly.
    """
    return {
        "credential": {
            "type": credential_type,
            "key": "*",
            "label": label,
            "fields": fields,
            "multi": True,
        }
    }
