"""Built-in node library.

Importing this module registers every built-in node into the default registry.
Nodes follow n8n's model: a single wired data input (triggers have none), config
parameters edited in the inspector, and one or more named outputs.
"""

import json
from typing import Any

from noodle.sdk import node

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


# ==========================================================================
# Triggers — entry points. They have no wired input; the runtime supplies the
# event that starts a run.
# ==========================================================================


@node(name="Manual Trigger", id="manual_trigger", category="Triggers", icon="play",
      inputs=[], params={"data": {"description": "Sample payload for test runs."}})
def manual_trigger(data: dict | None = None) -> dict:
    """Start the workflow on demand. Useful while building and testing."""
    return data or {}


@node(name="Schedule Trigger", id="schedule_trigger", category="Triggers", icon="clock",
      inputs=[], params={
          "interval": {"choices": ["minutes", "hours", "days"]},
          "every": {"description": "Run once per this many intervals."},
          "cron": {"placeholder": "0 9 * * 1-5", "description": "Optional cron expression."},
      })
def schedule_trigger(interval: str = "hours", every: int = 1, cron: str = "") -> dict:
    """Start the workflow on a fixed schedule."""
    return {"interval": interval, "every": every, "cron": cron}


@node(name="Webhook", id="webhook_trigger", category="Triggers", icon="webhook",
      inputs=[], params={
          "http_method": {"choices": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
          "path": {"placeholder": "my-webhook", "description": "Last segment of the URL."},
          "response_mode": {"choices": ["On Received", "Last Node"]},
          "response_code": {"description": "HTTP status returned to the caller."},
      })
def webhook_trigger(
    http_method: str = "POST",
    path: str = "noodle",
    response_mode: str = "On Received",
    response_code: int = 200,
) -> dict:
    """Start the workflow from an inbound HTTP request to a unique URL."""
    return {}


# ==========================================================================
# Logic — branching and merging
# ==========================================================================


@node(name="If", id="if", category="Logic", icon="branch", outputs=["true", "false"],
      params={
          "field": {"placeholder": "status", "description": "Field to test (blank = whole input)."},
          "operator": {"choices": OPERATORS},
          "value": {"placeholder": "expected value"},
      })
def if_node(input: Any = None, field: str = "", operator: str = "is true",
            value: str = "") -> dict:
    """Route the input to the true or false branch based on a condition."""
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


@node(name="Filter", id="filter", category="Logic", icon="filter", params={
    "field": {"placeholder": "status"},
    "operator": {"choices": OPERATORS},
    "value": {"placeholder": "expected value"},
})
def filter_node(input: Any = None, field: str = "", operator: str = "is not empty",
                value: str = "") -> list:
    """Keep only the input items that satisfy a condition."""
    return [it for it in _as_list(input) if _matches(_field(it, field), operator, value)]


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


@node(name="Code", id="code", category="Transform", icon="code", params={
    "code": {"multiline": True,
             "description": "Python code. `input` is in scope; assign the result to `output`."},
})
def code_node(input: Any = None, code: str = "output = input") -> Any:
    """Run arbitrary Python against the input."""
    namespace: dict[str, Any] = {"input": input}
    exec(code, namespace)  # noqa: S102 - running user Python is the node's purpose
    return namespace.get("output")


@node(name="HTTP Request", id="http_request", category="Transform", icon="globe", params={
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
})
def http_request(input: Any = None, url: str = "", method: str = "GET",
                 headers: dict | None = None, query: dict | None = None,
                 body: dict | None = None) -> Any:
    """Call an HTTP API and return the JSON body (or text on non-JSON)."""
    import requests

    response = requests.request(
        method,
        url,
        headers=headers or None,
        params=query or None,
        json=body or None,
        timeout=30,
    )
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
