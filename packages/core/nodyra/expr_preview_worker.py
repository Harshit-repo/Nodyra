"""Expression-preview worker: JSON-lines over stdin/stdout.

Runs user ``{{ }}`` expressions OUTSIDE the API process so a sandbox escape
in ``nodyra.expr`` lands in a process with no secrets, no DB access, and a
minimal environment. Must never import ``app.*`` or read config.

Protocol (one JSON object per line):
  request:  {"op": "eval", "value": str, "json": any, "inputs": {}, "nodes": {}}
            {"op": "probe_env", "key": str}        # diagnostics / tests
  response: {"result": any, "error": str|null, "parts": [...]}
            {"value": str|null}                     # probe_env
"""

import json
import os
import sys
from typing import Any

from nodyra.expr import build_context, evaluate, evaluate_parts
from nodyra.serialization import serialize_value


def _unwrap(value: Any) -> Any:
    """Pull the raw data out of an ``expr._Attrible`` wrapper so it serializes."""
    inner = getattr(value, "_data", None)
    return inner if inner is not None else value


def _serialize_part(part: dict[str, Any]) -> dict[str, Any]:
    out = dict(part)
    if "value" in out:
        out["value"] = serialize_value(_unwrap(out["value"]))
    return out


def _handle_eval(req: dict[str, Any]) -> dict[str, Any]:
    context = build_context(
        first_input=req.get("json"),
        inputs=req.get("inputs") or {},
        node_outputs=req.get("nodes") or {},
    )
    parts = [_serialize_part(p) for p in evaluate_parts(req["value"], context)]
    try:
        result = evaluate(req["value"], context)
    except Exception as exc:  # noqa: BLE001 - any eval failure becomes a payload
        return {"result": None, "error": f"{type(exc).__name__}: {exc}", "parts": parts}
    # ``evaluate`` swallows errors into "[expr error: ...]" strings.
    if isinstance(result, str) and result.startswith("[expr error:"):
        return {"result": None, "error": result.strip("[]"), "parts": parts}
    return {"result": serialize_value(_unwrap(result)), "error": None, "parts": parts}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("op") == "probe_env":
                resp: dict[str, Any] = {"value": os.environ.get(req["key"])}
            else:
                resp = _handle_eval(req)
        except Exception as exc:  # noqa: BLE001 - protocol must never die silently
            resp = {"result": None, "error": f"worker: {type(exc).__name__}: {exc}", "parts": []}
        sys.stdout.write(json.dumps(resp, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
