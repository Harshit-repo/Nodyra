"""Facts about how this process was actually launched.

Distinct from ``app.config``, which describes what an operator asked for.
These read the live server, so they stay true when someone starts uvicorn by
hand with different flags than the shipped deployments use.
"""

from __future__ import annotations

import sys


def proxy_headers_enabled() -> bool:
    """True when the ASGI server may rewrite the client address from headers.

    uvicorn's ``--proxy-headers`` (on by default) replaces ``scope["client"]``
    with the address in X-Forwarded-For for any peer in its trusted set —
    ``127.0.0.1`` unless told otherwise — and keeps no copy of the original.
    Anything relying on the socket peer being the real caller has to know.

    Returns False when uvicorn is not running the process at all: under the
    test client, or another ASGI server, nothing rewrites the address.
    """
    uvicorn = sys.modules.get("uvicorn")
    if uvicorn is None:
        return False

    server = getattr(getattr(uvicorn, "Server", None), "_nodyra_current", None)
    config = getattr(server, "config", None)
    if config is not None:
        return bool(getattr(config, "proxy_headers", False))

    # No server instance to ask (uvicorn imported but not serving, or a
    # version that does not expose one). Reflect the CLI, which is what the
    # shipped deployments set.
    argv = sys.argv
    if "--no-proxy-headers" in argv:
        return False
    if "--proxy-headers" in argv:
        return True
    # uvicorn's own default is on.
    return any("uvicorn" in str(arg) for arg in argv[:2])
