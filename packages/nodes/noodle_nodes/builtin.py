"""Built-in node library.

Importing this module registers every built-in node into the default registry.
A node has a single wired data input (triggers have none), config parameters
edited in the inspector, and one or more named outputs.
"""

import base64
import hashlib
import hmac
import json
from types import ModuleType
from typing import Any

from noodle.context import node_debug
from noodle.sdk import node
from noodle_nodes._creds import cred_multi, cred_single
from noodle_nodes.http_security import safe_request

OPERATORS = [
    "equals",
    "not equals",
    "contains",
    "greater than",
    "less than",
    "is empty",
    "is not empty",
    "is true",
]

CONVERSION_TARGETS = ["int", "float", "string", "boolean", "list", "json", "object"]


def _field(value: Any, field: str) -> Any:
    """Read ``field`` from ``value`` when it is a dict, else return ``value``."""
    if field and isinstance(value, dict):
        return value.get(field)
    return value


def _matches(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "is empty":
        return actual in (None, "", [], {})
    if operator == "is not empty":
        return actual not in (None, "", [], {})
    if operator == "is true":
        return bool(actual)
    if operator == "equals":
        return str(actual) == str(expected)
    if operator == "not equals":
        return str(actual) != str(expected)
    if operator == "contains":
        return str(expected).lower() in str(actual).lower()
    if operator in ("greater than", "less than"):
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return left > right if operator == "greater than" else left < right
    return False


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


import re as _re
from datetime import datetime as _datetime


def _matches_typed(actual: Any, dtype: str, operator: str, value: str = "") -> bool:
    if dtype == "string":
        s = str(actual) if actual is not None else ""
        v = str(value)
        if operator == "equals":           return s == v
        if operator == "not equals":       return s != v
        if operator == "contains":         return v.lower() in s.lower()
        if operator == "does not contain": return v.lower() not in s.lower()
        if operator == "starts with":      return s.lower().startswith(v.lower())
        if operator == "ends with":        return s.lower().endswith(v.lower())
        if operator == "is empty":         return s == ""
        if operator == "is not empty":     return s != ""
        if operator == "matches regex":
            try:
                return bool(_re.search(v, s))
            except _re.error:
                return False

    elif dtype == "number":
        try:
            n = float(actual)
            v_num = float(value)
        except (TypeError, ValueError):
            return False
        if operator == "equals":                 return n == v_num
        if operator == "not equals":             return n != v_num
        if operator == "greater than":           return n > v_num
        if operator == "greater than or equal":  return n >= v_num
        if operator == "less than":              return n < v_num
        if operator == "less than or equal":     return n <= v_num

    elif dtype == "boolean":
        # Coerce: the string literals "false"/"0"/"no"/"off"/"" are falsy.
        if isinstance(actual, bool):
            b = actual
        elif isinstance(actual, str):
            b = actual.lower() not in ("false", "0", "no", "off", "")
        else:
            b = bool(actual)
        if operator == "is true":  return b
        if operator == "is false": return not b

    elif dtype == "array":
        arr = actual if isinstance(actual, list) else []
        if operator == "is empty":         return len(arr) == 0
        if operator == "is not empty":     return len(arr) > 0
        if operator == "contains":         return str(value) in [str(x) for x in arr]
        if operator == "does not contain": return str(value) not in [str(x) for x in arr]
        try:
            v_len = int(value)
        except (TypeError, ValueError):
            return False
        if operator == "length equals":       return len(arr) == v_len
        if operator == "length not equals":   return len(arr) != v_len
        if operator == "length greater than": return len(arr) > v_len
        if operator == "length less than":    return len(arr) < v_len

    elif dtype == "object":
        obj = actual if isinstance(actual, dict) else {}
        if operator == "has key":           return str(value) in obj
        if operator == "does not have key": return str(value) not in obj
        if operator == "is empty":          return len(obj) == 0
        if operator == "is not empty":      return len(obj) > 0

    elif dtype == "date":
        try:
            d_actual = _datetime.fromisoformat(str(actual))
            d_value = _datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        if operator == "before": return d_actual < d_value
        if operator == "after":  return d_actual > d_value
        if operator == "equals": return d_actual.date() == d_value.date()

    elif dtype == "any":
        if operator == "exists":         return actual is not None
        if operator == "does not exist": return actual is None
        if operator == "is empty":       return actual in (None, "", [], {})
        if operator == "is not empty":   return actual not in (None, "", [], {})

    return False


def _eval_conditions(input_data: Any, conditions_param: Any) -> bool:
    if not isinstance(conditions_param, dict):
        return False
    logic = str(conditions_param.get("logic", "AND")).upper()
    conditions = conditions_param.get("conditions") or []

    def _check(cond):
        field = str(cond.get("field", ""))
        dtype = str(cond.get("type", "any"))
        operator = str(cond.get("operator", "exists"))
        value = str(cond.get("value", ""))
        actual = _field(input_data, field) if field else input_data
        return _matches_typed(actual, dtype, operator, value)

    valid = [c for c in conditions if isinstance(c, dict)]
    if not valid:
        return True  # vacuous true on no valid conditions
    if logic == "AND":
        return all(_check(c) for c in valid)
    return any(_check(c) for c in valid)


# ==========================================================================
# Triggers — entry points. They have no wired input; the runtime supplies the
# event that starts a run.
# ==========================================================================


@node(name="Manual Trigger", id="manual_trigger", category="Triggers", icon="play",
      role="trigger",
      inputs=[], params={"data": {"description": "Sample payload for test runs."}})
def manual_trigger(data: dict | None = None) -> dict:
    """Start the workflow on demand. Useful while building and testing."""
    return data or {}


@node(name="Schedule Trigger", id="schedule_trigger", category="Triggers", icon="clock",
      role="trigger",
      inputs=[], params={
          "interval": {"choices": ["minutes", "hours", "days"]},
          "every": {"description": "Run once per this many intervals."},
          "cron": {
              "group": "Options",
              "placeholder": "0 9 * * 1-5",
              "description": "Optional cron expression.",
          },
          "tz": {
              "group": "Options",
              "placeholder": "UTC",
              "description": (
                  "Timezone for the cron expression — IANA name like "
                  "America/New_York or Asia/Kolkata. Defaults to UTC."
              ),
          },
      })
def schedule_trigger(
    interval: str = "hours",
    every: int = 1,
    cron: str = "",
    tz: str = "UTC",
) -> dict:
    """Start the workflow on a fixed schedule."""
    return {"interval": interval, "every": every, "cron": cron, "tz": tz}


@node(name="Webhook", id="webhook_trigger", category="Triggers", icon="webhook",
      role="trigger",
      inputs=[], params={
          # --- Core (always shown) ---
          "http_method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
          "path": {
              "placeholder": "products/{id}",
              "description": (
                  "URL path for this webhook. Supports REST-style templates with "
                  "{param} segments, e.g. 'products/{id}' or "
                  "'customers/{id}/orders'. Captured values are exposed on the "
                  "trigger output as $json.params (e.g. {{ $json.params.id }}). "
                  "A plain path with no {param} matches that exact URL."
              ),
          },
          "response_mode": {
              "choices": ["On Received", "Last Node", "Respond Node"],
              "description": (
                  "When to respond: 'On Received' acks immediately; 'Last Node' "
                  "waits and returns the final node's output; 'Respond Node' "
                  "waits and returns whatever a Respond to Webhook node records."
              ),
          },
          "response_code": {"description": "HTTP status returned to the caller."},
          # --- Authentication (optional group) ---
          "auth_type": {
              "group": "Authentication",
              "choices": ["none", "basic", "header", "query", "bearer", "jwt"],
              "description": (
                  "Authentication required for callers. 'none' accepts any "
                  "request. 'basic' = HTTP Basic Auth. 'header' = check a custom "
                  "header. 'query' = check a query-string parameter. 'bearer' = "
                  "Authorization: Bearer <token>. 'jwt' = verify an HS256 JWT."
              ),
          },
          "auth_jwt_header": {
              "group": "Authentication",
              "placeholder": "Authorization",
              "description": "Header carrying the JWT (auth_type=jwt). Default Authorization.",
          },
          "auth_credentials": {
              "group": "Authentication",
              # Default credential type for static manifest; the inspector
              # dynamically swaps this based on auth_type — Basic Auth uses
              # http_basic (username/password), Header Auth uses http_header
              # (name/value), Query Auth uses http_query (name/value).
              **cred_multi(
                  "http_basic",
                  "HTTP Basic Auth credentials",
                  ["username", "password"],
              ),
              "description": "Stored credential used to authenticate inbound webhook calls.",
          },
          # --- Security (optional group, independent of auth_type) ---
          # Signature verification: when 'on', the raw request body is
          # HMAC-verified against a shared secret before the workflow runs.
          "hmac_verification": {"group": "Security", "choices": ["off", "on"]},
          "hmac_header": {
              "group": "Security",
              "placeholder": "X-Signature",
              "description": "Header carrying the HMAC signature.",
          },
          "hmac_algorithm": {"group": "Security", "choices": ["sha256", "sha1"]},
          "hmac_prefix": {
              "group": "Security",
              "placeholder": "sha256=",
              "description": "Optional prefix stripped from the signature header (e.g. 'sha256=').",
          },
          "hmac_secret": {
              "group": "Security",
              **cred_single("hmac", "secret", "HMAC shared secret"),
              "description": "Shared secret used to verify the HMAC signature.",
          },
          # IP allowlist: non-empty → callers outside the listed CIDRs/IPs are
          # rejected with 403 before any auth check.
          "ip_allowlist": {
              "group": "Security",
              "placeholder": "203.0.113.0/24, 198.51.100.7",
              "description": (
                  "Comma/newline-separated CIDRs or IPs allowed to call this "
                  "webhook. Blank = allow all."
              ),
          },
          "trust_proxy": {
              "group": "Security",
              "choices": ["off", "on"],
              "description": (
                  "When 'on', honour the left-most X-Forwarded-For entry for the "
                  "IP allowlist (set only behind a trusted proxy). Default 'off' "
                  "uses the socket peer."
              ),
          },
          # --- Idempotency (optional group) ---
          "dedup": {"group": "Idempotency", "choices": ["off", "on"]},
          "dedup_key": {
              "group": "Idempotency",
              "placeholder": "{{ $json.headers['x-delivery-id'] }}",
              "description": (
                  "Expression evaluated against the request to identify a unique "
                  "delivery. A repeat value is acknowledged without re-running."
              ),
          },
          # --- Body (optional group) ---
          # Raw body capture: when 'on', the exact request bytes are written as
          # an artifact and exposed as a `raw_body` ref on the trigger output, so
          # binary/multipart uploads reach the workflow without bloating the DB.
          "raw_body": {"group": "Body", "choices": ["off", "on"]},
          # --- Response shaping (optional group; On Received mode) ---
          "response_data": {
              "group": "Response",
              "choices": ["First Entry JSON", "All Entries", "No Body", "Custom"],
              "description": (
                  "Shape the immediate response (On Received mode). Blank = a "
                  "default JSON ack with the run id."
              ),
          },
          "response_body": {
              "group": "Response",
              "placeholder": "{{ $json.body }}",
              "description": "Custom response body expression (response_data=Custom).",
          },
          "response_headers": {
              "group": "Response",
              "key_value": True,
              "description": "Custom response headers (response_data=Custom).",
          },
      })
def webhook_trigger(
    http_method: str = "POST",
    path: str = "noodle",
    response_mode: str = "On Received",
    response_code: int = 200,
    response_data: str = "",
    response_body: str = "",
    response_headers: dict | None = None,
    auth_type: str = "none",
    auth_credentials: dict | None = None,
    auth_jwt_header: str = "Authorization",
    hmac_verification: str = "off",
    hmac_header: str = "X-Signature",
    hmac_algorithm: str = "sha256",
    hmac_prefix: str = "",
    hmac_secret: str | None = None,
    ip_allowlist: str = "",
    trust_proxy: str = "off",
    dedup: str = "off",
    dedup_key: str = "",
    raw_body: str = "off",
) -> dict:
    """Start the workflow from an inbound HTTP request to a unique URL.

    Auth options: none, HTTP Basic (Authorization header), custom header,
    or a query-string token. Credentials should always come from the
    Credentials store rather than be pasted inline.
    """
    return {}


@node(name="API Endpoint", id="api_endpoint", category="Triggers", icon="webhook",
      role="trigger",
      inputs=[], outputs=["main"], params={
          "base_path": {
              "placeholder": "customers",
              "description": (
                  "Base path for this API. Routes below are matched under "
                  "/webhook/<base_path>/… , e.g. base 'customers' + route "
                  "'GET /{id}' serves GET /webhook/customers/42."
              ),
          },
          "routes": {
              "widget": "routes_table",
              "description": (
                  "Route table: a list of {method, path, output} rows. Each row "
                  "maps an HTTP method + sub-path template (e.g. 'GET /{id}', "
                  "'POST /', 'GET /{id}/orders') to a named output branch. The "
                  "single most-specific matching route fires; captured path "
                  "params are exposed on the branch as $json.params."
              ),
          },
          "response_mode": {
              "choices": ["Last Node", "Respond Node"],
              "description": (
                  "How the matched branch responds: 'Last Node' returns the "
                  "branch's final node output; 'Respond Node' returns whatever a "
                  "Respond to Webhook node in the branch records."
              ),
          },
          "response_code": {
              "description": "Default HTTP status when a branch doesn't set one.",
          },
          # --- Authentication (optional group; same shape as the Webhook node) ---
          "auth_type": {
              "group": "Authentication",
              "choices": ["none", "basic", "header", "query", "bearer", "jwt"],
              "description": (
                  "Authentication required for callers (same options as the "
                  "Webhook node)."
              ),
          },
          "auth_jwt_header": {
              "group": "Authentication",
              "placeholder": "Authorization",
              "description": "Header carrying the JWT (auth_type=jwt). Default Authorization.",
          },
          "auth_credentials": {
              "group": "Authentication",
              **cred_multi(
                  "http_basic",
                  "HTTP Basic Auth credentials",
                  ["username", "password"],
              ),
              "description": "Stored credential used to authenticate inbound API calls.",
          },
      })
def api_endpoint(
    base_path: str = "",
    routes: list | None = None,
    response_mode: str = "Last Node",
    response_code: int = 200,
    auth_type: str = "none",
    auth_jwt_header: str = "Authorization",
    auth_credentials: dict | None = None,
) -> dict:
    """Serve a REST-style API from a single workflow.

    Define a base path and a table of routes; each route maps an HTTP method and
    sub-path template to a named output branch. An inbound request fires the one
    most-specific matching branch with the request payload (path params on
    ``$json.params``); the branch's response goes back to the caller per
    ``response_mode``. The node is a router, so exactly one branch runs.
    """
    return {}


@node(name="Error Trigger", id="error_trigger", category="Triggers", icon="alert",
      role="trigger",
      inputs=[], params={
          "error": {
              "description": (
                  "Sample error payload for testing an error workflow. Production "
                  "error workflows receive the failing workflow/run/node context."
              ),
          },
      })
def error_trigger(error: dict | None = None) -> dict:
    """Start an error-handling workflow with normalized failure context."""
    raw = error or {}
    if isinstance(raw, dict):
        message = raw.get("message") or raw.get("error") or "Workflow failed"
        return {
            "message": str(message),
            "node_id": raw.get("node_id"),
            "workflow_id": raw.get("workflow_id"),
            "run_id": raw.get("run_id"),
            "raw": raw,
        }
    return {
        "message": str(raw),
        "node_id": None,
        "workflow_id": None,
        "run_id": None,
        "raw": raw,
    }


@node(name="Chat Trigger", id="chat_trigger", category="Triggers", icon="chat",
      role="trigger",
      inputs=[],
      param_groups={"Options": [
          "initial_message", "input_placeholder", "title",
          "public_access", "require_login",
      ]},
      params={
          "initial_message": {
              "widget": "textarea", "group": "Options",
              "description": "Assistant greeting shown when the chat panel opens.",
          },
          "input_placeholder": {
              "group": "Options", "placeholder": "Type a message…",
              "description": "Placeholder text for the chat message box.",
          },
          "title": {
              "group": "Options", "placeholder": "Chat",
              "description": "Header label for the chat panel.",
          },
          "public_access": {
              "type": "boolean", "group": "Options", "default": False,
              "description": "Enable the hosted chat page for this workflow.",
          },
          "require_login": {
              "type": "boolean", "group": "Options", "default": True,
              "description": "Require visitors to sign into this Noodle instance.",
          },
          "chat_token": {
              "type": "string", "widget": "hidden", "default": "",
          },
      })
def chat_trigger(initial_message: str = "", input_placeholder: str = "",
                 title: str = "", public_access: bool = False,
                 require_login: bool = True, chat_token: str = "") -> dict:
    """Conversational entry point. When a chat turn runs, the chat service seeds
    this node's output with the user's message and session id; on a plain manual
    run it returns the empty shape so the graph stays runnable."""
    return {"chatInput": "", "sessionId": ""}


# ==========================================================================
# Logic — branching and merging
# ==========================================================================


@node(name="If", id="if", category="Logic", icon="branch", outputs=["true", "false"],
      params={
          "conditions": {
              "widget": "conditions_builder",
              "description": (
                  "Typed multi-condition test. Each row specifies a field path, "
                  "data type, operator, and comparison value. Use the logic toggle "
                  "to require ALL (AND) or ANY (OR) conditions to match."
              ),
          },
          "field": {
              "placeholder": "status",
              "description": "Field to test (blank = whole input). Legacy: use Conditions above.",
              "group": "Legacy condition",
          },
          "operator": {
              "choices": OPERATORS,
              "group": "Legacy condition",
          },
          "value": {
              "placeholder": "expected value",
              "group": "Legacy condition",
          },
      })
def if_node(
    input: Any = None,
    field: str = "",
    operator: str = "is true",
    value: str = "",
    conditions: dict | None = None,
) -> dict:
    """Route the input to the true or false branch based on a condition."""
    if conditions is not None:
        matched = _eval_conditions(input, conditions)
    else:
        matched = _matches(_field(input, field), operator, value)
    return {"true": input} if matched else {"false": input}


@node(name="Switch", id="switch", category="Logic", icon="switch",
      outputs=["fallback"], params={
          "field": {
              "placeholder": "type",
              "description": "Field whose value selects the branch.",
          },
          "rules": {
              "description": (
                  "Each entry creates an output branch — the key is the branch "
                  "name, the value is what the field is matched against."
              ),
              "key_value": True,
          },
      })
def switch_node(
    input: Any = None, field: str = "", rules: dict | None = None
) -> dict:
    """Route the input to one of several branches by matching a field value."""
    rules = rules or {}
    actual = str(_field(input, field))
    for branch, expected in rules.items():
        if actual == str(expected):
            return {branch: input}
    return {"fallback": input}


@node(name="Filter", id="filter", category="Logic", icon="filter",
      params={
          "conditions": {
              "widget": "conditions_builder",
              "description": (
                  "Typed multi-condition filter. Items passing all (AND) or any "
                  "(OR) conditions flow through; others are dropped."
              ),
          },
          "field": {
              "placeholder": "status",
              "group": "Legacy condition",
          },
          "operator": {
              "choices": OPERATORS,
              "group": "Legacy condition",
          },
          "value": {
              "placeholder": "expected value",
              "group": "Legacy condition",
          },
      })
def filter_node(
    input: Any = None,
    field: str = "",
    operator: str = "is not empty",
    value: str = "",
    conditions: dict | None = None,
) -> Any:
    """Keep only the input items that satisfy a condition.

    When the upstream input is a single object (not a list), a passing filter
    returns that object directly so the next node receives the same shape it
    would have received without the filter.  When the input is already a list,
    the output is always a list (possibly empty).
    """
    input_was_list = isinstance(input, list)
    if conditions is not None:
        results = [it for it in _as_list(input) if _eval_conditions(it, conditions)]
    else:
        results = [it for it in _as_list(input) if _matches(_field(it, field), operator, value)]
    if not input_was_list and len(results) == 1:
        return results[0]
    return results


@node(name="Merge", id="merge", category="Logic", icon="merge",
      inputs=["input_a", "input_b"], params={
          "mode": {"choices": ["append", "combine"],
                   "description": "append: concatenate · combine: pair into one list."},
      })
def merge_node(input_a: Any = None, input_b: Any = None, mode: str = "append") -> list:
    """Merge two input streams into one."""
    if mode == "combine":
        return [input_a, input_b]
    merged: list = []
    for value in (input_a, input_b):
        if isinstance(value, list):
            merged.extend(value)
        elif value is not None:
            merged.append(value)
    return merged


@node(name="Loop Over Items", id="loop_over_items", category="Logic", icon="repeat",
      deprecated=True, replacement_id="loop_start",
      outputs=["item", "done"], params={
          "max_items": {
              "description": "Optional maximum number of items to emit (0 = all).",
          },
      })
def loop_over_items(input: Any = None, max_items: int = 0) -> dict:
    """DEPRECATED — use Loop Start / Loop End for real per-item iteration.

    Noodle's current DAG engine executes a node once per run rather than once
    per item. This node therefore emits the selected items as a list on the
    ``item`` output while also emitting a completion summary on ``done``. The
    Loop Start / Loop End nodes run the in-between sub-DAG once per row, which
    is what most "loop" use cases actually want.
    """
    items = _as_list(input)
    limit = int(max_items or 0)
    if limit > 0:
        items = items[:limit]
    return {"item": items, "done": {"items": items, "count": len(items)}}


@node(
    name="Loop Start",
    id="loop_start",
    category="Logic",
    icon="repeat",
    outputs=["item", "index", "state"],
    params={
        "mode": {
            "description": (
                "each = one row per iteration; batch = a list of N rows; "
                "group = rows sharing a key; range = loop a fixed count; "
                "window = overlapping sliding windows of N rows; "
                "while/until = loop on a condition, carrying state across iterations."
            ),
            "choices": ["each", "batch", "group", "range", "window", "while", "until"],
            "display_name": "Mode",
        },
        "concurrency": {
            "description": (
                "How many iterations to process at once (default 1). "
                "Forced to 1 for while/until."
            ),
        },
        "on_error": {
            "description": (
                "fail = stop the loop on the first failing iteration; "
                "continue = collect errors and keep going."
            ),
            "choices": ["fail", "continue"],
        },
        "max_rows": {
            "description": (
                "Maximum input rows allowed before the loop fails "
                "(each/batch/group; default 10000)."
            ),
        },
        "batch_size": {
            "description": (
                "Rows per iteration when mode=batch (last may be shorter) "
                "or window size when mode=window."
            ),
        },
        "group_key": {
            "description": (
                "Row field to group by when mode=group; "
                "each iteration gets {key, rows}."
            ),
        },
        "count": {
            "description": "Number of iterations when mode=range.",
        },
        "start": {
            "description": "First value when mode=range (default 0).",
        },
        "step": {
            "description": (
                "Step between values (mode=range) or window slide "
                "(mode=window); default 1."
            ),
        },
        "accumulate": {
            "description": (
                "Reduce: thread an accumulator (seeded by `initial`) "
                "across for-each iterations; Loop End returns the final "
                "accumulator."
            ),
        },
        "initial": {
            "description": "Seed state for mode=while/until (any value or expression).",
        },
        "condition": {
            "description": (
                "Expression checked each iteration for while/until, "
                "e.g. {{ state.count < 10 }}."
            ),
        },
        "max_iterations": {
            "description": "Safety cap on while/until iterations (default 1000).",
        },
        "on_max_iterations": {
            "description": (
                "When the cap is hit: fail = raise; "
                "stop = emit the current state and warn."
            ),
            "choices": ["fail", "stop"],
        },
    },
)
def loop_start(
    input: Any = None,
    mode: str = "each",
    concurrency: int = 1,
    on_error: str = "fail",
    max_rows: int = 10000,
    batch_size: int = 1,
    group_key: str = "",
    count: int = 0,
    start: int = 0,
    step: int = 1,
    accumulate: bool = False,
    initial: Any = None,
    condition: str = "",
    max_iterations: int = 1000,
    on_max_iterations: str = "fail",
) -> dict[str, Any]:
    """Start of a loop region. The engine drives this node and runs the
    nodes between it and the paired Loop End once per iteration; this function
    is never called directly."""
    raise RuntimeError(
        "loop_start is executed by the engine's loop driver, not called directly"
    )


@node(
    name="Loop End",
    id="loop_end",
    category="Logic",
    icon="repeat",
    outputs=["results", "errors"],
    params={
        "loop_start_id": {
            "widget": "hidden",
            "description": "Auto-managed id of the paired Loop Start.",
        },
        "output_mode": {
            "description": (
                "records = a list of each row's result; dataset = a DatasetRef "
                "(each result must be a dict / object). For-each modes only."
            ),
            "choices": ["records", "dataset"],
            "display_name": "Output",
        },
        "conditional_output": {
            "description": (
                "while/until only: final_state = the last accumulator value; "
                "all_states = {final, states:[...]}."
            ),
            "choices": ["final_state", "all_states"],
            "display_name": "Conditional output",
        },
    },
)
def loop_end(
    input: Any = None,
    loop_start_id: str = "",
    output_mode: str = "records",
    conditional_output: str = "final_state",
) -> dict[str, Any]:
    """End of a loop region. The engine collects each row's value here; this
    function is never called directly."""
    raise RuntimeError(
        "loop_end is executed by the engine's loop driver, not called directly"
    )


@node(name="Stop And Error", id="stop_and_error", category="Logic", icon="alert",
      params={
          "message": {
              "placeholder": "Workflow stopped intentionally",
              "description": "Error message shown on the failed node/run.",
          },
      })
def stop_and_error(input: Any = None, message: str = "Workflow stopped intentionally") -> Any:
    """Intentionally fail the workflow, similar to n8n's Stop And Error node."""
    if isinstance(input, dict) and not message:
        message = str(
            input.get("message")
            or input.get("error")
            or "Workflow stopped intentionally"
        )
    raise RuntimeError(message or "Workflow stopped intentionally")


# ==========================================================================
# Data
# ==========================================================================


@node(name="Edit Fields", id="edit_fields", category="Data", icon="pencil", params={
    "fields": {
        "description": (
            "Fields to set on each item. Drag a field from the Input panel "
            "into a value to insert an expression like {{ $json.field }}."
        ),
        "key_value": True,
    },
    "keep_only_set": {"description": "Drop every field except the ones set here."},
})
def edit_fields(input: Any = None, fields: dict | None = None,
                keep_only_set: bool = False) -> Any:
    """Set, add, or replace fields on the input."""
    fields = fields or {}

    def apply(item: Any) -> dict:
        base = {} if keep_only_set else dict(item if isinstance(item, dict) else {})
        base.update(fields)
        return base

    if isinstance(input, list):
        return [apply(item) for item in input]
    return apply(input)


@node(name="Sort", id="sort", category="Data", icon="sort", params={
    "field": {"placeholder": "name", "description": "Field to sort by (blank = whole item)."},
    "order": {"choices": ["ascending", "descending"]},
})
def sort_node(input: Any = None, field: str = "", order: str = "ascending") -> list:
    """Sort a list of items by a field."""
    items = _as_list(input)
    reverse = order == "descending"

    def key(item: Any) -> Any:
        return item.get(field) if field and isinstance(item, dict) else item

    try:
        return sorted(items, key=key, reverse=reverse)
    except TypeError:
        return sorted(items, key=lambda item: str(key(item)), reverse=reverse)


@node(name="Limit", id="limit", category="Data", icon="limit", params={
    "max_items": {"description": "Maximum number of items to keep."},
    "keep": {"choices": ["first", "last"]},
})
def limit_node(input: Any = None, max_items: int = 10, keep: str = "first") -> list:
    """Keep only the first or last N items."""
    items = _as_list(input)
    count = max(int(max_items), 0)
    return items[-count:] if keep == "last" else items[:count]


@node(name="Aggregate", id="aggregate", category="Data", icon="aggregate", params={
    "field": {"placeholder": "email", "description": "Field to collect across all items."},
})
def aggregate_node(input: Any = None, field: str = "") -> dict:
    """Collapse a list of items into a single item."""
    items = _as_list(input)
    if field:
        return {field: [it.get(field) for it in items if isinstance(it, dict)]}
    return {"items": items}


@node(name="Remove Duplicates", id="remove_duplicates", category="Data", icon="dedupe",
      params={"field": {"placeholder": "id",
                        "description": "Field to compare (blank = whole item)."}})
def remove_duplicates(input: Any = None, field: str = "") -> list:
    """Drop items that have already been seen."""
    seen: set = set()
    result: list = []
    for item in _as_list(input):
        marker = item.get(field) if field and isinstance(item, dict) else item
        if isinstance(marker, (str, int, float, bool)) or marker is None:
            key = marker
        else:
            key = json.dumps(marker, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


@node(name="Rename Keys", id="rename_keys", category="Data", icon="tag", params={
    "mapping": {"description": 'Old-to-new key names, e.g. {"oldName": "newName"}.'},
})
def rename_keys(input: Any = None, mapping: dict | None = None) -> Any:
    """Rename keys on the input objects."""
    mapping = mapping or {}

    def rename(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        return {mapping.get(k, k): v for k, v in item.items()}

    if isinstance(input, list):
        return [rename(item) for item in input]
    return rename(input)


# ==========================================================================
# Transform
# ==========================================================================


def _short_repr(value: Any, limit: int = 140) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - debugger preview should never fail a node
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def _json_preview(value: Any, *, depth: int = 2, max_items: int = 20) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth <= 0:
        return _short_repr(value)
    if isinstance(value, (list, tuple, set)):
        items = list(value)[:max_items]
        preview = [
            _json_preview(item, depth=depth - 1, max_items=max_items)
            for item in items
        ]
        if len(value) > max_items:
            preview.append(f"... {len(value) - max_items} more")
        return preview
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= max_items:
                preview["..."] = f"{len(value) - max_items} more"
                break
            preview[str(key)] = _json_preview(
                item, depth=depth - 1, max_items=max_items
            )
        return preview
    try:
        return str(value)
    except (TypeError, ValueError):
        return _short_repr(value)


def _dataframe_variable(name: str, value: Any) -> dict[str, Any] | None:
    if type(value).__name__ != "DataFrame":
        return None
    try:
        rows, cols = value.shape
        columns = [str(col) for col in value.columns]
        preview = value.head(50).to_dict("records")
        dtypes = {str(col): str(dtype) for col, dtype in value.dtypes.items()}
    except Exception:  # noqa: BLE001 - fall back to generic variable preview
        return None
    return {
        "name": name,
        "type": "DataFrame",
        "summary": f"{int(rows)} rows x {int(cols)} columns",
        "shape": [int(rows), int(cols)],
        "columns": columns,
        "dtypes": dtypes,
        "preview": _json_preview(preview, depth=3, max_items=50),
    }


def _series_variable(name: str, value: Any) -> dict[str, Any] | None:
    if type(value).__name__ != "Series":
        return None
    try:
        length = int(len(value))
        preview = value.head(50).tolist()
    except Exception:  # noqa: BLE001 - fall back to generic variable preview
        return None
    return {
        "name": name,
        "type": "Series",
        "summary": f"{length} values",
        "shape": [length],
        "preview": _json_preview(preview, depth=2, max_items=50),
    }


def _variable_info(name: str, value: Any) -> dict[str, Any] | None:
    if name == "__builtins__" or name.startswith("__"):
        return None
    if isinstance(value, ModuleType) or callable(value):
        return None

    dataframe = _dataframe_variable(name, value)
    if dataframe is not None:
        return dataframe

    series = _series_variable(name, value)
    if series is not None:
        return series

    type_name = type(value).__name__
    info: dict[str, Any] = {
        "name": name,
        "type": type_name,
        "summary": _short_repr(value),
        "preview": _json_preview(value),
    }
    try:
        if isinstance(value, (list, tuple, set, dict, str)):
            info["length"] = len(value)
    except Exception:  # noqa: BLE001 - optional debugger metadata
        pass
    return info


def _record_code_variables(namespace: dict[str, Any]) -> None:
    debug = node_debug.get()
    if debug is None:
        return
    variables = []
    for name, value in namespace.items():
        info = _variable_info(name, value)
        if info is not None:
            variables.append(info)
    debug["variables"] = variables


def _collect_code_outputs(namespace: dict[str, Any]) -> tuple[bool, Any]:
    """Pick output ports from a Code namespace.

    Convention: ``output = ...`` becomes the ``main`` port; any
    ``output_<name> = ...`` becomes a port named ``<name>``. When only
    ``output`` is set, the single value is returned (single-output node).
    When any ``output_*`` is set, the engine receives a dict keyed by port
    name so the multi-output normalizer can dispatch it correctly.
    """
    extras: dict[str, Any] = {}
    for key, value in namespace.items():
        if not key.startswith("output_") or len(key) <= len("output_"):
            continue
        suffix = key[len("output_") :]
        if not suffix or not suffix.replace("_", "").isalnum():
            continue
        extras[suffix] = value
    has_main = "output" in namespace
    if not extras:
        return False, namespace.get("output")
    bundle: dict[str, Any] = {}
    if has_main:
        bundle["main"] = namespace.get("output")
    bundle.update(extras)
    return True, bundle


def discover_code_output_ports(code: str) -> list[str]:
    """Statically parse Code source and return the output ports it assigns.

    The editor calls this on every code change to populate the node's
    ``outputs_override`` so handles render before the workflow runs.
    Returns ``["main"]`` when only ``output`` is assigned (or when parsing
    fails / nothing is assigned — single-port is the safe default).
    """
    import ast as _ast

    try:
        tree = _ast.parse(code or "", mode="exec")
    except SyntaxError:
        return ["main"]

    ports: list[str] = []
    seen: set[str] = set()
    has_main = False
    for stmt in tree.body:
        targets: list[_ast.AST] = []
        if isinstance(stmt, _ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, (_ast.AugAssign, _ast.AnnAssign)):
            targets = [stmt.target]
        for tgt in targets:
            if not isinstance(tgt, _ast.Name):
                continue
            name = tgt.id
            if name == "output":
                has_main = True
                continue
            if not name.startswith("output_") or len(name) <= len("output_"):
                continue
            suffix = name[len("output_") :]
            if not suffix or not suffix.replace("_", "").isalnum():
                continue
            if suffix in seen:
                continue
            seen.add(suffix)
            ports.append(suffix)
    if not ports:
        return ["main"]
    return (["main"] if has_main else []) + ports


# Footgun guard, NOT a security boundary. These blocks raise ImportError to
# steer users toward Noodle's built-in nodes for process/FFI work and to catch
# accidental misuse. They do NOT contain a determined caller: `os.system`,
# `os.popen`, `open`, importable C-extension libs, and getattr-based reach all
# remain available by design (Code nodes are "arbitrary code on the runner
# host", see app.services.unsafe_nodes — they are flagged unconditionally unsafe
# and gated by `unsafe_node_policy`). The real isolation boundaries are:
#   1. process isolation (PROCESS_ISOLATED_NODE_TYPES → ProcessPoolExecutor /
#      the per-env runtime subprocess), and
#   2. the deployment-time `unsafe_node_policy` gate (warn/require_approval/block).
# Hardening toward an actual sandbox (container/seccomp per run) is tracked in
# docs/production-readiness-audit.md (SEC-1).
_CODE_NODE_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "subprocess",
    "pty",
    "ctypes",
    "cffi",
    "multiprocessing",
})


def _make_sandboxed_import(original_import: Any) -> Any:
    """Return an __import__ replacement that blocks the footgun modules.

    See ``_CODE_NODE_BLOCKED_IMPORTS`` — this is a guard, not a sandbox.
    """
    def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root in _CODE_NODE_BLOCKED_IMPORTS:
            raise ImportError(
                f"Module '{name}' is not available in the code sandbox. "
                "Use Noodle's built-in nodes for process execution."
            )
        return original_import(name, *args, **kwargs)
    return _safe_import


def _build_safe_builtins() -> dict:
    import builtins as _builtins
    b = vars(_builtins).copy()
    b["__import__"] = _make_sandboxed_import(_builtins.__import__)
    return b


# Computed once per worker process — reused across all code node executions
# in the same ProcessPoolExecutor worker to avoid re-copying ~155 builtins
# entries on every task.
_SAFE_BUILTINS: dict = _build_safe_builtins()


def _run_code_isolated(input: Any, code: str) -> Any:
    """Top-level picklable worker for ProcessPoolExecutor.

    Runs user code in the AST sandbox.  Deliberately omits artifacts_api —
    artifact writes are not supported from inside an isolated code node; the
    return value is the only channel back to the engine.
    """
    import ast
    import traceback as _tb

    from noodle.expr import _CodeValidator

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"SyntaxError in code node: {exc}") from exc

    visitor = _CodeValidator()
    try:
        visitor.visit(tree)
    except ValueError as exc:
        raise ValueError(f"Unsafe code: {exc}") from exc

    namespace: dict[str, Any] = {"input": input, "__builtins__": _SAFE_BUILTINS}
    try:
        exec(compile(tree, "<code_node>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        exc.add_note(_tb.format_exc())
        raise
    _is_multi, value = _collect_code_outputs(namespace)
    return value


@node(name="Code", id="code", category="Transform", icon="code", params={
    "code": {
        "multiline": True,
        "description": (
            "Python executed against the upstream value (variable `input`).\n\n"
            "Assign your result to `output` — do NOT use `return`. For "
            "multiple output ports, also assign `output_<name>` variables:\n\n"
            "    output = clean_rows\n"
            "    output_rejected = bad_rows\n"
            "    output_summary = {\"clean\": len(clean_rows)}\n\n"
            "Each `output_<name>` becomes a separate handle on the node."
        ),
        "placeholder": "output = input",
    },
})
def code_node(input: Any = None, code: str = "output = input") -> Any:
    """Run arbitrary Python against the input (process-isolated + AST sandboxed).

    The engine routes this node type through ProcessPoolExecutor automatically
    (PROCESS_ISOLATED_NODE_TYPES contains "code").  _run_code_isolated is the
    real worker — this wrapper exists so the @node decorator and debug capture
    still apply when called directly in tests.
    """
    namespace: dict[str, Any] = {"input": input}
    try:
        result = _run_code_isolated(input, code)
        namespace["output"] = result
        return result
    except Exception:
        raise
    finally:
        _record_code_variables(namespace)


# Transient statuses worth retrying when the caller opts into retries: rate
# limiting and gateway/upstream hiccups. 4xx other than 429 are the caller's
# problem and are never retried.
_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


def _http_backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 1s, 2s, 4s, … capped at 30s."""
    return min(2.0 ** attempt, 30.0)


@node(name="HTTP Request", id="http_request", category="API", icon="globe", params={
    "url": {"placeholder": "https://api.example.com/data"},
    "method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
    "headers": {
        "description": "Request headers — add fields, or switch to raw JSON.",
        "key_value": True,
    },
    "query": {
        "description": "Query string parameters — add fields, or switch to raw JSON.",
        "key_value": True,
    },
    "body": {
        "description": "JSON request body — add fields, or switch to raw JSON.",
        "key_value": True,
    },
    "timeout_seconds": {
        "group": "Options",
        "description": "Per-request timeout in seconds.",
    },
    "max_retries": {
        "group": "Options",
        "description": (
            "Retries on a transient failure (429/5xx or a connection/timeout "
            "error) with exponential backoff. 0 = a single attempt."
        ),
    },
})
def http_request(input: Any = None, url: str = "", method: str = "GET",
                 headers: dict | None = None, query: dict | None = None,
                 body: dict | None = None, timeout_seconds: float = 30,
                 max_retries: int = 0) -> Any:
    """Call an HTTP API and return the JSON body (or text on non-JSON).

    Optionally retries transient failures (429/5xx, connection/timeout errors)
    with exponential backoff; ``max_retries=0`` (default) makes a single attempt.
    """
    import time

    import requests

    timeout = float(timeout_seconds or 30)
    attempts = max(0, int(max_retries or 0)) + 1
    response = None
    for attempt in range(attempts):
        try:
            # safe_request re-validates every redirect hop against the SSRF
            # guard and disables ``requests``' unchecked auto-redirect (C2).
            response = safe_request(
                method,
                url,
                context="http_request",
                headers=headers or None,
                params=query or None,
                json=body or None,
                timeout=timeout,
            )
        except requests.RequestException:
            # Network-level failure: retry while attempts remain, else re-raise
            # the original error unchanged (default path keeps today's behaviour).
            if attempt + 1 < attempts:
                time.sleep(_http_backoff_seconds(attempt))
                continue
            raise
        if (
            response.status_code in _RETRYABLE_HTTP_STATUS
            and attempt + 1 < attempts
        ):
            time.sleep(_http_backoff_seconds(attempt))
            continue
        break
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code >= 400:
        detail = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        if len(detail) > 500:
            detail = f"{detail[:500]}..."
        reason = getattr(response, "reason", "") or ""
        label = f"HTTP {response.status_code}"
        if reason:
            label = f"{label} {reason}"
        raise RuntimeError(f"{label} from {url}: {detail}")
    return payload


@node(name="GraphQL Request", id="graphql_request", category="API", icon="globe", params={
    "url": {"placeholder": "https://api.example.com/graphql"},
    "query": {"multiline": True, "description": "GraphQL query or mutation."},
    "variables": {"description": "GraphQL variables object.", "key_value": True},
    "headers": {"description": "Request headers.", "key_value": True},
    "timeout_seconds": {"group": "Options", "description": "Per-request timeout in seconds."},
})
def graphql_request(
    input: Any = None,
    url: str = "",
    query: str = "",
    variables: dict | None = None,
    headers: dict | None = None,
    timeout_seconds: float = 30,
) -> Any:
    """Execute a GraphQL query/mutation over HTTP POST."""
    import requests

    if not url:
        raise ValueError("graphql_request: url is required")
    if not query:
        raise ValueError("graphql_request: query is required")
    request_variables = (
        variables if variables is not None else (input if isinstance(input, dict) else {})
    )
    response = safe_request(
        "POST",
        url,
        context="graphql_request",
        headers=headers or None,
        json={"query": query, "variables": request_variables},
        timeout=float(timeout_seconds or 30),
    )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code >= 400:
        detail = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        raise RuntimeError(f"HTTP {response.status_code} from {url}: {detail}")
    if isinstance(payload, dict) and payload.get("errors"):
        errors = json.dumps(payload["errors"], default=str)
        raise RuntimeError(f"GraphQL errors from {url}: {errors}")
    return payload


@node(
    name="Respond to Webhook",
    id="respond_to_webhook",
    category="API",
    icon="webhook",
    params={
        "status_code": {
            "group": "Options",
            "description": "HTTP status code for the webhook response.",
        },
        "headers": {"group": "Options", "description": "Response headers.", "key_value": True},
        "body_field": {
            "placeholder": "payload",
            "description": (
                "Optional field to read from an object input; blank returns the whole input."
            ),
        },
    },
)
def respond_to_webhook(
    input: Any = None,
    status_code: int = 200,
    headers: dict | None = None,
    body_field: str = "",
) -> dict:
    """Shape the HTTP response body/status for a webhook-triggered workflow."""
    body = input.get(body_field, input) if body_field and isinstance(input, dict) else input
    return {"status_code": int(status_code), "headers": headers or {}, "body": body}


_JWT_ALGORITHMS = {
    "HS256": hashlib.sha256,
    "HS384": hashlib.sha384,
    "HS512": hashlib.sha512,
}


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode((payload + padding).encode("ascii"))


@node(name="JWT", id="jwt", category="API", icon="key", params={
    "operation": {"choices": ["sign", "verify", "decode"]},
    "secret": {"description": "HMAC secret for sign/verify. Not required for decode."},
    "algorithm": {"group": "Options", "choices": ["HS256", "HS384", "HS512"]},
})
def jwt_node(
    input: Any = None,
    operation: str = "decode",
    secret: str = "",
    algorithm: str = "HS256",
) -> Any:
    """Sign, verify, or decode HMAC JWTs without external dependencies."""
    if algorithm not in _JWT_ALGORITHMS:
        raise ValueError(f"unsupported JWT algorithm {algorithm!r}")
    if operation == "sign":
        claims = input if isinstance(input, dict) else {"value": input}
        header = {"typ": "JWT", "alg": algorithm}
        signing_input = ".".join(
            [
                _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                _b64url_encode(
                    json.dumps(claims, separators=(",", ":"), default=str).encode("utf-8")
                ),
            ]
        )
        digest = hmac.new(
            secret.encode("utf-8"),
            signing_input.encode("ascii"),
            _JWT_ALGORITHMS[algorithm],
        ).digest()
        return f"{signing_input}.{_b64url_encode(digest)}"

    if not isinstance(input, str):
        raise ValueError("jwt: input must be a token string for decode/verify")
    parts = input.split(".")
    if len(parts) != 3:
        raise ValueError("jwt: token must have header.payload.signature")
    header = json.loads(_b64url_decode(parts[0]))
    payload = json.loads(_b64url_decode(parts[1]))
    if operation == "verify":
        token_algorithm = header.get("alg") or algorithm
        if token_algorithm not in _JWT_ALGORITHMS:
            raise ValueError(f"unsupported JWT algorithm {token_algorithm!r}")
        signing_input = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(
            secret.encode("utf-8"),
            signing_input.encode("ascii"),
            _JWT_ALGORITHMS[token_algorithm],
        ).digest()
        actual = _b64url_decode(parts[2])
        if not hmac.compare_digest(expected, actual):
            raise ValueError("jwt: signature verification failed")
    elif operation != "decode":
        raise ValueError(f"unknown JWT operation {operation!r}")
    return {**payload, "header": header}


@node(name="Date & Time", id="datetime", category="Transform", icon="calendar", params={
    "operation": {"choices": ["current timestamp", "format"]},
    "date_format": {"placeholder": "%Y-%m-%d %H:%M:%S"},
})
def datetime_node(input: Any = None, operation: str = "current timestamp",
                  date_format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Produce or format the current date and time."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    if operation == "format":
        return now.strftime(date_format)
    return now.isoformat()


@node(name="To JSON", id="to_json", category="Transform", icon="braces", params={
    "indent": {"description": "Indentation width for the JSON output."},
})
def to_json(input: Any = None, indent: int = 2) -> str:
    """Serialize the input to a JSON string."""
    return json.dumps(input, indent=int(indent), default=str)


@node(name="From JSON", id="from_json", category="Transform", icon="import")
def from_json(input: Any = "") -> Any:
    """Parse a JSON string into a Python value."""
    if isinstance(input, str) and input.strip():
        return json.loads(input)
    return input


# ==========================================================================
# Utility
# ==========================================================================


@node(name="No Operation", id="no_op", category="Utility", icon="dot")
def no_op(input: Any = None) -> Any:
    """Pass the input through unchanged."""
    return input


@node(name="Wait", id="wait", category="Utility", icon="pause", params={
    "seconds": {"description": "Seconds to pause before continuing (max 30)."},
})
def wait_node(input: Any = None, seconds: int = 1) -> Any:
    """Pause the workflow, then pass the input through."""
    import time

    time.sleep(min(max(float(seconds), 0.0), 30.0))
    return input


# ==========================================================================
# String / list helpers (stdlib-only)
# ==========================================================================


@node(name="Join", id="join", category="Data", icon="merge", params={
    "separator": {"placeholder": ", ", "description": "Inserted between items."},
})
def join_node(input: Any = None, separator: str = ", ") -> str:
    """Join a list of items into a single string."""
    items = input if isinstance(input, list) else ([] if input is None else [input])
    return separator.join(str(item) for item in items)


@node(name="Split", id="split", category="Data", icon="switch", params={
    "separator": {"placeholder": ",", "description": "Substring to split on."},
    "max_split": {
        "description": "Maximum number of splits (-1 for unlimited).",
    },
})
def split_node(input: str = "", separator: str = ",", max_split: int = -1) -> list:
    """Split a string into a list."""
    text = str(input or "")
    limit = int(max_split) if int(max_split) > 0 else -1
    return text.split(separator, limit)


@node(name="Length", id="length", category="Data", icon="ruler")
def length_node(input: Any = None) -> int:
    """Return the length of a string, list, or dict."""
    if input is None:
        return 0
    try:
        return len(input)
    except TypeError:
        return len(str(input))


@node(name="Regex Extract", id="regex_extract", category="Transform", icon="regex",
      params={
          "pattern": {
              "placeholder": r"(\d+)",
              "description": "Python regex. Groups become individual matches.",
          },
      })
def regex_extract(input: str = "", pattern: str = "") -> list:
    """Return every regex match in the input as a list."""
    import re

    if not pattern:
        return []
    return re.findall(pattern, str(input or ""))


@node(name="Regex Replace", id="regex_replace", category="Transform", icon="regex",
      params={
          "pattern": {"placeholder": r"\s+", "description": "Python regex to replace."},
          "replacement": {"placeholder": " ", "description": "Replacement text."},
      })
def regex_replace(input: str = "", pattern: str = "", replacement: str = "") -> str:
    """Replace every regex match in the input."""
    import re

    if not pattern:
        return str(input or "")
    return re.sub(pattern, replacement, str(input or ""))


@node(name="Hash", id="hash", category="Transform", icon="hash", params={
    "algorithm": {"choices": ["md5", "sha1", "sha256", "sha512"]},
})
def hash_node(input: Any = None, algorithm: str = "sha256") -> str:
    """Hash the input with one of the common digests."""
    import hashlib

    digest = hashlib.new(algorithm)
    digest.update(str(input if input is not None else "").encode("utf-8"))
    return digest.hexdigest()


@node(name="UUID", id="uuid", category="Transform", icon="tag")
def uuid_v4(input: Any = None) -> str:  # noqa: ARG001 - input ignored
    """Generate a new UUID4."""
    import uuid

    return str(uuid.uuid4())


@node(name="Base64 Encode", id="base64_encode", category="Transform", icon="braces")
def base64_encode(input: Any = "") -> str:
    """Encode the input as a base64 string."""
    import base64

    payload = input if isinstance(input, (bytes, bytearray)) else str(input or "").encode("utf-8")
    return base64.b64encode(bytes(payload)).decode("ascii")


@node(name="Base64 Decode", id="base64_decode", category="Transform", icon="import")
def base64_decode(input: str = "") -> str:
    """Decode a base64 string to text."""
    import base64

    try:
        return base64.b64decode(str(input or "")).decode("utf-8", "replace")
    except (ValueError, base64.binascii.Error):
        return ""


# ==========================================================================
# Sub-workflows
# ==========================================================================

MAX_MAP_ITEMS = 10_000
MAX_MAP_CONCURRENCY = 50


def _bounded_map_concurrency(node_id: str, concurrency: int | None) -> int:
    worker_count = max(1, int(concurrency or 5))
    if worker_count > MAX_MAP_CONCURRENCY:
        raise ValueError(
            f"{node_id}: concurrency must be <= {MAX_MAP_CONCURRENCY}."
        )
    return worker_count


@node(
    name="Execute Workflow",
    id="execute_workflow",
    category="Logic",
    icon="merge",
    params={
        "workflow_id": {
            "placeholder": "id of another workflow",
            "description": "The workflow id to run as a sub-step.",
        },
    },
)
async def execute_workflow_node(
    input: Any = None, workflow_id: str = ""
) -> Any:
    """Run another Noodle workflow as a step and return its result."""
    from noodle.context import workflow_caller

    if not workflow_id:
        raise ValueError("execute_workflow: workflow_id is required")
    caller = workflow_caller.get()
    if caller is None:
        raise RuntimeError(
            "execute_workflow: no host caller is configured for this run"
        )
    return await caller(workflow_id, input)


@node(
    name="Map Items",
    id="map_items",
    category="Logic",
    icon="repeat",
    outputs=["main", "errors"],
    params={
        "workflow_id": {
            "description": "ID of the workflow to call once per item.",
            "placeholder": "workflow id",
        },
        "concurrency": {
            "description": "Maximum concurrent child workflow calls (default 5).",
        },
        "on_error": {
            "description": (
                "fail = stop on first item error; "
                "continue = collect errors on the errors output."
            ),
            "choices": ["fail", "continue"],
        },
        "preserve_order": {
            "description": "Return results in input order (default true).",
        },
    },
)
async def map_items(
    input: Any = None,
    workflow_id: str = "",
    concurrency: int = 5,
    on_error: str = "fail",
    preserve_order: bool = True,
) -> dict[str, Any]:
    """Call a child workflow once per item in a list and collect results."""
    import asyncio

    from noodle.context import workflow_caller
    from noodle_nodes._map import _map_call_child

    if not workflow_id:
        raise ValueError("map_items: workflow_id is required")
    caller = workflow_caller.get()
    if caller is None:
        raise RuntimeError("map_items: no host caller is configured for this run")

    items: list = input if isinstance(input, list) else ([] if input is None else [input])
    if len(items) > MAX_MAP_ITEMS:
        raise ValueError(
            f"map_items received {len(items)} items but the hard fan-out cap is "
            f"{MAX_MAP_ITEMS}."
        )

    # Multi-tenancy C5: every mapped item becomes a child workflow run.
    from noodle.context import org_run_limits

    org_cap = int((org_run_limits.get() or {}).get("max_map_width") or 0)
    if org_cap and len(items) > org_cap:
        raise ValueError(
            f"map_items received {len(items)} items but this organization's "
            f"map fan-out cap is {org_cap}."
        )
    sem = asyncio.Semaphore(_bounded_map_concurrency("map_items", concurrency))

    tasks = [
        _map_call_child(
            caller=caller,
            workflow_id=workflow_id,
            payload={"item": item, "index": i},
            index=i,
            sem=sem,
        )
        for i, item in enumerate(items)
    ]
    raw = await asyncio.gather(*tasks)
    ordered = sorted(raw, key=lambda r: r["index"]) if preserve_order else list(raw)

    if on_error == "fail":
        for r in ordered:
            if not r["ok"]:
                raise RuntimeError(
                    f"map_items: item {r['index']} failed: {r['error']}"
                )

    successful = [r["result"] for r in ordered if r["ok"]]
    errors = [
        {"index": r["index"], "error": r["error"], "input": r["input"]}
        for r in ordered
        if not r["ok"]
    ]
    return {"main": successful, "errors": errors}


@node(
    name="Map Group",
    id="map_group",
    category="Logic",
    icon="repeat",
    outputs=["main", "errors"],
    params={
        "child_workflow_id": {
            "widget": "hidden",
            "description": "Auto-managed child workflow ID.",
        },
        "mode": {
            "widget": "hidden",
            "description": "inline or reference",
        },
        "concurrency": {
            "description": "Maximum concurrent child workflow calls (default 5).",
        },
        "on_error": {
            "description": (
                "fail = stop on first item error; "
                "continue = collect errors on the errors output."
            ),
            "choices": ["fail", "continue"],
        },
        "preserve_order": {
            "description": "Return results in input order (default true).",
        },
        "max_items": {
            "description": "Maximum items allowed before the node fails (default 10000).",
        },
    },
)
async def map_group_node(
    input: Any = None,
    child_workflow_id: str = "",
    mode: str = "inline",
    concurrency: int = 5,
    on_error: str = "fail",
    preserve_order: bool = True,
    max_items: int = 10000,
) -> dict[str, Any]:
    """Run the map body workflow once per item in the input list."""
    import asyncio

    from noodle.context import workflow_caller
    from noodle_nodes._map import _map_call_child

    if not child_workflow_id:
        raise ValueError("map_group: child_workflow_id is not set — save the workflow first")
    caller = workflow_caller.get()
    if caller is None:
        raise RuntimeError("map_group: no host caller is configured for this run")

    items: list = input if isinstance(input, list) else ([] if input is None else [input])
    cap = max(1, int(max_items or 10000))
    if cap > MAX_MAP_ITEMS:
        raise ValueError(
            f"map_group: max_items must be <= {MAX_MAP_ITEMS}."
        )
    if len(items) > cap:
        raise ValueError(
            f"Map Group received {len(items)} items but max_items is {cap}. "
            f"Increase max_items explicitly or reduce the list upstream."
        )

    sem = asyncio.Semaphore(_bounded_map_concurrency("map_group", concurrency))
    tasks = [
        _map_call_child(
            caller=caller,
            workflow_id=child_workflow_id,
            payload={"item": item, "index": i},
            index=i,
            sem=sem,
        )
        for i, item in enumerate(items)
    ]
    raw = await asyncio.gather(*tasks)
    ordered = sorted(raw, key=lambda r: r["index"]) if preserve_order else list(raw)

    if on_error == "fail":
        for r in ordered:
            if not r["ok"]:
                raise RuntimeError(f"map_group: item {r['index']} failed: {r['error']}")

    successful = [r["result"] for r in ordered if r["ok"]]
    errors = [
        {"index": r["index"], "error": r["error"], "input": r["input"]}
        for r in ordered
        if not r["ok"]
    ]
    return {"main": successful, "errors": errors}


# ---- Data type conversions -----------------------------------------------
#
# Lenient coercions with sensible defaults. Each node has an explicit
# docstring (which becomes the description in the inspector) so users know
# what's happening at the boundary — types are duck-typed at the engine
# level so the value goes through unchanged unless you opt into one of
# these casts.

_TRUTHY = {"true", "yes", "y", "on", "1"}
_FALSY = {"false", "no", "n", "off", "0", ""}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _TRUTHY:
            return True
        if s in _FALSY:
            return False
        raise ValueError(f"can't interpret {value!r} as a boolean")
    return bool(value)


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        # Allow "3.0" / "3.14" → 3 by routing through float first.
        return int(float(s))
    raise ValueError(f"can't convert {type(value).__name__} to int")


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        return float(s) if s else 0.0
    raise ValueError(f"can't convert {type(value).__name__} to float")


def _convert_value(value: Any, target: str, *, separator: str = ",") -> Any:
    if target == "int":
        return _to_int(value)
    if target == "float":
        return _to_float(value)
    if target == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            try:
                return json.dumps(value, default=str)
            except (TypeError, ValueError):
                return str(value)
        return "" if value is None else str(value)
    if target == "boolean":
        return _to_bool(value)
    if target == "list":
        return to_list_node(value, separator=separator)
    if target == "json":
        return json.dumps(value, default=str)
    if target == "object":
        if isinstance(value, str):
            return json.loads(value)
        if isinstance(value, (dict, list)):
            return value
        raise ValueError(
            f"can't parse {type(value).__name__} as a JSON object"
        )
    raise ValueError(f"unknown target type {target!r}")


@node(name="To Integer", id="to_int", category="Data Types", icon="hash")
def to_int_node(input: Any = None) -> int:
    """Coerce the input to an int.

    Strings parse via ``int(float(...))`` so ``"3"`` and ``"3.14"`` both work
    (the latter truncates to 3). ``None`` and empty strings become 0. Bools
    are 0/1. Anything else raises.
    """
    return _to_int(input)


@node(name="To Float", id="to_float", category="Data Types", icon="hash")
def to_float_node(input: Any = None) -> float:
    """Coerce the input to a float. ``None``/empty string → 0.0; bools → 0.0/1.0."""
    return _to_float(input)


@node(name="To String", id="to_str", category="Data Types", icon="tag")
def to_str_node(input: Any = None) -> str:
    """Coerce the input to a string.

    Dicts and lists serialize as JSON; everything else uses ``str()``.
    """
    if input is None:
        return ""
    if isinstance(input, str):
        return input
    if isinstance(input, (dict, list, tuple)):
        try:
            return json.dumps(input, default=str)
        except (TypeError, ValueError):
            return str(input)
    return str(input)


@node(name="To Boolean", id="to_bool", category="Data Types", icon="branch")
def to_bool_node(input: Any = None) -> bool:
    """Coerce the input to a bool.

    Strings recognise ``true/false``, ``yes/no``, ``y/n``, ``on/off``, ``1/0``
    (case-insensitive, trimmed). Numbers use ``!= 0``. Empty string → False.
    Anything else raises so a typo doesn't silently turn into ``True``.
    """
    return _to_bool(input)


@node(name="To List", id="to_list", category="Data Types", icon="ruler", params={
    "separator": {
        "placeholder": ",",
        "description": (
            "Used only when the input is a string. Split on this; "
            "an empty separator wraps the value in a single-element list."
        ),
    },
})
def to_list_node(input: Any = None, separator: str = ",") -> list:
    """Coerce the input to a list.

    Already-a-list values pass through. Tuples become lists. Dicts become
    their list-of-key:value-pairs. A string is split by ``separator``; if
    ``separator`` is blank the whole string becomes a single-element list.
    Anything else is wrapped as ``[value]``.
    """
    if isinstance(input, list):
        return input
    if isinstance(input, tuple):
        return list(input)
    if isinstance(input, dict):
        return [{"key": k, "value": v} for k, v in input.items()]
    if isinstance(input, str):
        if not separator:
            return [input]
        return [piece.strip() for piece in input.split(separator)]
    if input is None:
        return []
    return [input]


@node(name="Convert Type", id="convert_type", category="Data Types", icon="braces", params={
    "to": {
        "choices": CONVERSION_TARGETS,
        "description": (
            "Target type. 'json' returns a JSON string; "
            "'object' parses a JSON string into a dict/list."
        ),
    },
})
def convert_type_node(input: Any = None, to: str = "string") -> Any:
    """One node that handles every cast. Useful when the target type is wired in dynamically.

    For the common cases, prefer the dedicated ``to_int``/``to_str``/etc.
    nodes — they read more clearly on the canvas.
    """
    try:
        return _convert_value(input, to)
    except ValueError as exc:
        raise ValueError(f"convert_type: {exc}") from exc


@node(
    name="Convert Fields",
    id="convert_fields",
    category="Data Types",
    icon="braces",
    params={
        "conversions": {
            "description": (
                "Field-to-type map. Example: price=float, active=boolean, id=int."
            ),
            "key_value": True,
            "choices": CONVERSION_TARGETS,
        },
        "separator": {
            "placeholder": ",",
            "description": "Used only when a field is converted to list.",
        },
    },
)
def convert_fields_node(
    input: Any = None,
    conversions: dict | None = None,
    separator: str = ",",
) -> Any:
    """Convert selected fields on one object or every object in a list.

    ``conversions`` maps field names to target types: int, float, string,
    boolean, list, json, or object. Missing fields are left unchanged.
    """
    conversions = conversions or {}
    if not conversions:
        return input

    def convert_row(row: dict, row_index: int | None = None) -> dict:
        out = dict(row)
        for field, target in conversions.items():
            field_name = str(field)
            if field_name not in out:
                continue
            target_type = str(target)
            try:
                out[field_name] = _convert_value(
                    out[field_name],
                    target_type,
                    separator=separator,
                )
            except Exception as exc:  # noqa: BLE001 - surface field context
                location = f" row {row_index}" if row_index is not None else ""
                raise ValueError(
                    f"convert_fields:{location} field {field_name!r} value "
                    f"{out[field_name]!r} can't convert to {target_type!r}: {exc}"
                ) from exc
        return out

    if isinstance(input, list):
        result: list = []
        for index, item in enumerate(input):
            if isinstance(item, dict):
                result.append(convert_row(item, index))
            else:
                result.append(item)
        return result
    if isinstance(input, dict):
        return convert_row(input)
    raise ValueError(
        f"convert_fields: expected an object or list of objects, got "
        f"{type(input).__name__}"
    )


@node(
    name="Set Field Type",
    id="set_field_type",
    category="Data Types",
    icon="braces",
    params={
        "field": {
            "placeholder": "price",
            "description": (
                "Dot-path of the field to convert (e.g. 'price' or 'meta.count'). "
                "One level of nesting is supported. Leave blank to pass through unchanged."
            ),
        },
        "to": {
            "choices": CONVERSION_TARGETS,
            "description": "Target type for the field.",
        },
    },
)
def set_field_type_node(input: Any = None, field: str = "", to: str = "string") -> Any:
    """Convert a single named field of the input object to the specified type.

    The rest of the input passes through unchanged.  Supports one level of
    dot-notation for nested fields (e.g. ``meta.count``).  If the field is
    absent or the input is not a dict, the input is returned as-is.
    """
    if not field or not isinstance(input, dict):
        return input
    result = dict(input)
    parts = field.split(".", 1)
    if len(parts) == 1:
        if field in result:
            result[field] = _convert_value(result[field], to)
    else:
        parent, child = parts
        if parent in result and isinstance(result[parent], dict) and child in result[parent]:
            result[parent] = {
                **result[parent],
                child: _convert_value(result[parent][child], to),
            }
    return result
