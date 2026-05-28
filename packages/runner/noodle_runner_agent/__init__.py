"""Noodle remote runner agent.

A standalone, lightweight daemon that registers a machine as a Noodle worker
and runs workflows dispatched by the API. It deliberately does NOT depend on
``noodle-core`` — the Noodle packages are installed into each managed venv,
not into the agent process itself, so the agent stays small and its own
dependency surface (httpx + websockets) never collides with workflow code.
"""

__version__ = "0.0.1"
