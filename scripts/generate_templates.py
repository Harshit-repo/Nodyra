"""Author the curated workflow-template catalog and its preview thumbnails.

Templates are the product's highest-leverage acquisition channel and were its
weakest asset: four of them, where the tools people compare Nodyra against ship
hundreds. A template is also the fastest honest answer to "what can this do?" —
a runnable graph beats a feature list.

Everything here is written to run with **no credentials**, so a reader can open
one, press run, and see real output in the first minute. Credential-bearing
templates exist too (see webhook_to_slack) but they cannot be the first thing a
new user meets.

Run: ``uv run python scripts/generate_templates.py`` then
``uv run python scripts/validate_templates.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "apps" / "api" / "app" / "data" / "templates"
PREVIEW_DIR = ROOT / "apps" / "web" / "public" / "template-previews"

# Derived from VERSION so a release bump cannot leave every template
# advertising itself as incompatible with the build it ships in. The create
# dialog shows this string to the user on the first screen.
_VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
_MAJOR = _VERSION.split(".")[0]
COMPATIBILITY = f">={_MAJOR}.0.0,<{int(_MAJOR) + 1}.0.0"
CREATOR = "Nodyra maintainers"

# Node type → the accent used in its preview thumbnail. Grouped by role so a
# reader can tell a trigger from a transform at thumbnail size.
ROLE_COLOURS = {
    "trigger": "#9B7328",
    "transform": "#2E4A7D",
    "io": "#1F6B48",
    "logic": "#7A4B8C",
}

TRIGGER_TYPES = {
    "schedule_trigger",
    "webhook_trigger",
    "manual_trigger",
    "rss_feed_trigger",
    "file_watcher_trigger",
    "error_trigger",
}
LOGIC_TYPES = {"if", "filter", "switch", "loop_start", "loop_end", "stop_and_error", "merge"}
IO_TYPES = {
    "http_request",
    "csv_write",
    "read_csv_file",
    "read_json_file",
    "write_excel_file",
    "read_text_file",
    "pdf_extract_text_v2",
    "respond_to_webhook",
    "slack",
}


def _role(node_type: str) -> str:
    if node_type in TRIGGER_TYPES:
        return "trigger"
    if node_type in LOGIC_TYPES:
        return "logic"
    if node_type in IO_TYPES:
        return "io"
    return "transform"


def _node(node_id: str, node_type: str, params: dict, x: int, y: int = 40) -> dict:
    return {"id": node_id, "type": node_type, "params": params, "position": {"x": x, "y": y}}


def _chain(*specs) -> dict:
    """Build a linear graph from ``(id, type, params)`` triples."""
    nodes = [
        _node(node_id, node_type, params, x=index * 280)
        for index, (node_id, node_type, params) in enumerate(specs)
    ]
    edges = [{"source": specs[i][0], "target": specs[i + 1][0]} for i in range(len(specs) - 1)]
    return {"nodes": nodes, "edges": edges}


def preview_svg(template: dict) -> str:
    """A small node-chain diagram, drawn from the graph itself.

    Generated rather than hand-drawn so a thumbnail can never show a shape the
    template no longer has.
    """
    nodes = template["graph"]["nodes"][:5]
    width, height = 480, 120
    box_w, box_h, gap = 76, 40, 22
    total = len(nodes) * box_w + (len(nodes) - 1) * gap
    start_x = (width - total) / 2
    mid_y = height / 2 - box_h / 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="{template["name"]} workflow shape">',
        f'<rect width="{width}" height="{height}" fill="#F5F6F8"/>',
    ]
    for index, node in enumerate(nodes):
        x = start_x + index * (box_w + gap)
        colour = ROLE_COLOURS[_role(node["type"])]
        if index:
            line_start = x - gap
            parts.append(
                f'<line x1="{line_start:.0f}" y1="{height / 2:.0f}" x2="{x:.0f}" '
                f'y2="{height / 2:.0f}" stroke="#C3C9D4" stroke-width="1.5"/>'
            )
        parts.append(
            f'<rect x="{x:.0f}" y="{mid_y:.0f}" width="{box_w}" height="{box_h}" rx="5" '
            f'fill="#FFFFFF" stroke="{colour}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<rect x="{x:.0f}" y="{mid_y:.0f}" width="4" height="{box_h}" rx="2" fill="{colour}"/>'
        )
        label = node["type"].replace("_", " ")[:11]
        parts.append(
            f'<text x="{x + box_w / 2:.0f}" y="{height / 2 + 3:.0f}" text-anchor="middle" '
            f'font-family="IBM Plex Sans, system-ui, sans-serif" font-size="8.5" '
            f'fill="#3D4552">{label}</text>'
        )
    if len(template["graph"]["nodes"]) > 5:
        parts.append(
            f'<text x="{width - 12}" y="{height - 10}" text-anchor="end" '
            f'font-family="IBM Plex Sans, system-ui, sans-serif" font-size="9" '
            f'fill="#6B7484">+{len(template["graph"]["nodes"]) - 5} more</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── The catalog ────────────────────────────────────────────────────────────
# Each entry is deliberately small enough to read in one screen. A template
# nobody can follow teaches nothing.

TEMPLATES: list[dict] = [
    {
        "id": "http_health_check",
        "name": "HTTP health check with alert",
        "description": "Poll an endpoint on a schedule and fail the run loudly when it stops answering, "
        "so your alerting sees it.",
        "tags": ["monitoring", "http", "schedule", "starter"],
        "credential_free": True,
        "prerequisites": ["Outbound HTTPS to the endpoint you are checking"],
        "expected_result": "A green run each time the endpoint answers healthily, and a failed run naming "
        "the status code when it does not.",
        "permissions": ["Network: the URL you configure"],
        "graph": {
            "nodes": [
                {
                    "id": "every_5_min",
                    "type": "schedule_trigger",
                    "params": {"interval": "minutes", "every": 5},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "probe",
                    "type": "http_request",
                    "params": {
                        "url": "https://httpbin.org/json",
                        "method": "GET",
                        "timeout_seconds": 10,
                        "max_retries": 1,
                        "include_response_metadata": True,
                    },
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "check",
                    "type": "code",
                    "params": {
                        "code": "# The probe requests status, headers and body metadata.\n"
                        "status = input['status_code']\n"
                        "if not 200 <= status < 300:\n"
                        "    raise ValueError(f'health check failed: HTTP {status}')\n"
                        "body = input.get('body')\n"
                        "if body in (None, '', {}, []):\n"
                        "    raise ValueError('health check failed: empty "
                        "response body')\n"
                        "output = {'healthy': True, 'status_code': status, 'body': body}"
                    },
                    "position": {"x": 560, "y": 40},
                },
            ],
            "edges": [
                {"source": "every_5_min", "target": "probe"},
                {"source": "probe", "target": "check"},
            ],
        },
    },
    {
        "id": "csv_clean_dedupe",
        "name": "Clean and de-duplicate a CSV",
        "description": "Normalise sample customer records, remove duplicate email addresses, and export a "
        "cleaned CSV. Replace the sample node with your own records to use real data.",
        "tags": ["data", "csv", "cleanup", "starter"],
        "credential_free": True,
        "prerequisites": [],
        "expected_result": "A cleaned CSV artifact with duplicate rows removed.",
        "permissions": [],
        "graph": {
            "nodes": [
                {
                    "id": "start",
                    "type": "manual_trigger",
                    "params": {"data": {}},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "sample",
                    "type": "code",
                    "params": {
                        "code": "output = [\n"
                        "    {'name': 'Ada Lovelace', 'email': '  Ada@Example.COM "
                        "'},\n"
                        "    {'name': 'Grace Hopper', 'email': "
                        "'grace@example.com'},\n"
                        "    {'name': 'Ada L.', 'email': 'ada@example.com'},\n"
                        "    {'name': 'Alan Turing', 'email': ' "
                        "Alan@Example.com'},\n"
                        "]"
                    },
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "normalise",
                    "type": "string_normalize",
                    "params": {"columns": "email", "case": "lower", "trim": True},
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "to_records",
                    "type": "dataset_to_records",
                    "params": {"max_rows": 10000, "allow_truncate": False},
                    "position": {"x": 840, "y": 40},
                },
                {
                    "id": "dedupe",
                    "type": "remove_duplicates",
                    "params": {"field": "email"},
                    "position": {"x": 1120, "y": 40},
                },
                {
                    "id": "to_dataset",
                    "type": "records_to_dataset",
                    "params": {},
                    "position": {"x": 1400, "y": 40},
                },
                {
                    "id": "write",
                    "type": "csv_write",
                    "params": {"filename": "cleaned.csv", "include_header": True},
                    "position": {"x": 1680, "y": 40},
                },
            ],
            "edges": [
                {"source": "start", "target": "sample"},
                {"source": "sample", "target": "normalise"},
                {"source": "normalise", "target": "to_records"},
                {"source": "to_records", "target": "dedupe"},
                {"source": "dedupe", "target": "to_dataset"},
                {"source": "to_dataset", "target": "write"},
            ],
        },
    },
    {
        "id": "rss_digest",
        "name": "RSS feed to daily digest",
        "description": (
            "Watch an RSS feed, keep the newest entries, and render them into a "
            "single Markdown digest you can email or archive."
        ),
        "tags": ["rss", "content", "digest", "starter"],
        "prerequisites": ["Outbound HTTPS to the feed URL"],
        "expected_result": "One Markdown digest per run listing the latest entries.",
        "permissions": ["Network: the feed URL you configure"],
        "credential_free": True,
        "graph": _chain(
            (
                "feed",
                "rss_feed_trigger",
                {"feed_url": "https://news.ycombinator.com/rss", "max_items": 10},
            ),
            ("newest", "limit", {"max_items": 5, "keep": "first"}),
            (
                "render",
                "code",
                {
                    "code": (
                        "items = input if isinstance(input, list) else "
                        "(input or {}).get('items', [])\n"
                        "lines = [f\"- [{i.get('title','untitled')}]({i.get('link','')})\"\n"
                        "         for i in items]\n"
                        "output = {'markdown': '# Digest\\n\\n' + '\\n'.join(lines),\n"
                        "          'count': len(lines)}"
                    )
                },
            ),
        ),
    },
    {
        "id": "json_api_to_csv",
        "name": "JSON API to CSV export",
        "description": "Fetch a JSON list from an API, pick the fields you care about, sort it, and "
        "export a CSV artifact.",
        "tags": ["api", "csv", "export", "etl"],
        "credential_free": True,
        "prerequisites": ["Outbound HTTPS to the API you are calling"],
        "expected_result": "A CSV artifact containing the selected fields, sorted.",
        "permissions": ["Network: the API URL you configure"],
        "graph": {
            "nodes": [
                {
                    "id": "start",
                    "type": "manual_trigger",
                    "params": {"data": {}},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "fetch",
                    "type": "http_request",
                    "params": {
                        "url": "https://jsonplaceholder.typicode.com/users",
                        "method": "GET",
                    },
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "select",
                    "type": "code",
                    "params": {
                        "code": "body = input.get('body') if isinstance(input, dict) else "
                        "input\n"
                        "rows = body if isinstance(body, list) else []\n"
                        "output = [{'id': r.get('id'), 'name': r.get('name'),\n"
                        "           'email': r.get('email')} for r in rows]"
                    },
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "sort_rows",
                    "type": "sort",
                    "params": {"field": "name", "order": "ascending"},
                    "position": {"x": 840, "y": 40},
                },
                {
                    "id": "to_dataset",
                    "type": "records_to_dataset",
                    "params": {},
                    "position": {"x": 1120, "y": 40},
                },
                {
                    "id": "export",
                    "type": "csv_write",
                    "params": {"filename": "users.csv", "include_header": True},
                    "position": {"x": 1400, "y": 40},
                },
            ],
            "edges": [
                {"source": "start", "target": "fetch"},
                {"source": "fetch", "target": "select"},
                {"source": "select", "target": "sort_rows"},
                {"source": "sort_rows", "target": "to_dataset"},
                {"source": "to_dataset", "target": "export"},
            ],
        },
    },
    {
        "id": "webhook_validate_respond",
        "name": "Validate a webhook payload and reply",
        "description": "Accept an inbound webhook, validate it against a JSON schema, and answer 200 or "
        "422 so the caller learns immediately what was wrong.",
        "tags": ["webhook", "validation", "api"],
        "credential_free": True,
        "prerequisites": [
            "An inbound webhook URL reachable by the caller",
            "Configure webhook authentication before publishing; the production default "
            "requires it",
        ],
        "expected_result": "Valid payloads return 200; invalid ones return the validation errors rather "
        "than failing silently.",
        "permissions": ["Inbound: webhook"],
        "graph": {
            "nodes": [
                {
                    "id": "hook",
                    "type": "webhook_trigger",
                    "params": {
                        "path": "validated-intake",
                        "http_method": "POST",
                        "response_mode": "Respond Node",
                    },
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "body",
                    "type": "code",
                    "params": {"code": "output = (input or {}).get('body')"},
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "validate",
                    "type": "json_schema_validate",
                    "params": {
                        "schema": '{"type": "object", "required": ["email", "amount"], '
                        '"properties": {"email": {"type": "string"}, "amount": '
                        '{"type": "number"}}}'
                    },
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "valid",
                    "type": "if",
                    "params": {"field": "valid", "operator": "is true"},
                    "position": {"x": 840, "y": 40},
                },
                {
                    "id": "accepted",
                    "type": "respond_to_webhook",
                    "params": {"status_code": 200},
                    "position": {"x": 1120, "y": -40},
                },
                {
                    "id": "rejected",
                    "type": "respond_to_webhook",
                    "params": {"status_code": 422},
                    "position": {"x": 1120, "y": 160},
                },
            ],
            "edges": [
                {"source": "hook", "target": "body"},
                {"source": "body", "target": "validate"},
                {"source": "validate", "target": "valid"},
                {"source": "valid", "target": "accepted", "source_output": "true"},
                {"source": "valid", "target": "rejected", "source_output": "false"},
            ],
        },
    },
    {
        "id": "batch_http_fetch",
        "name": "Fetch many URLs in parallel",
        "description": "Loop over a list of URLs, fetch each one concurrently, and collect the successes "
        "and failures separately.",
        "tags": ["http", "loop", "batch", "concurrency"],
        "credential_free": True,
        "prerequisites": ["Outbound HTTPS to the URLs you supply"],
        "expected_result": "One results collection with every successful response, and a separate errors "
        "collection for the ones that failed.",
        "permissions": ["Network: the URLs you supply at run time"],
        "graph": {
            "nodes": [
                {
                    "id": "start",
                    "type": "manual_trigger",
                    "params": {
                        "data": {"urls": ["https://httpbin.org/json", "https://httpbin.org/uuid"]}
                    },
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "expand",
                    "type": "code",
                    "params": {"code": "output = (input or {}).get('urls', [])"},
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "each",
                    "type": "loop_start",
                    "params": {"mode": "each", "concurrency": 4, "on_error": "continue"},
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "fetch",
                    "type": "http_request",
                    "params": {"url": "{{ $json }}", "method": "GET", "timeout_seconds": 10},
                    "position": {"x": 840, "y": 40},
                },
                {
                    "id": "collect",
                    "type": "loop_end",
                    "params": {"loop_start_id": "each", "output_mode": "records"},
                    "position": {"x": 1120, "y": 40},
                },
            ],
            "edges": [
                {"source": "start", "target": "expand"},
                {"source": "expand", "target": "each"},
                {"source": "each", "target": "fetch", "source_output": "item"},
                {"source": "fetch", "target": "collect"},
            ],
        },
    },
    {
        "id": "data_quality_gate",
        "name": "Data quality gate",
        "description": "Profile an incoming dataset, flag statistical outliers, and stop the run before "
        "bad data reaches anything downstream.",
        "tags": ["data-quality", "validation", "etl"],
        "credential_free": False,
        "prerequisites": [
            "A CSV file readable by the Nodyra process",
            "pandas>=2.0, ydata-profiling>=4.0,<5, and setuptools>=78.1.1,<81 installed in the workflow environment",
        ],
        "expected_result": "A profile report plus a separate outliers collection; the run fails when "
        "outliers exceed your threshold.",
        "permissions": ["Filesystem: the input path you configure"],
        "graph": {
            "nodes": [
                {
                    "id": "start",
                    "type": "manual_trigger",
                    "params": {"data": {}},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "read",
                    "type": "read_csv_file",
                    "params": {"path": "data/metrics.csv", "has_header": True},
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "outliers",
                    "type": "outlier_detect_statistical",
                    "params": {"column": "value", "method": "zscore", "threshold": 3.0},
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "profile",
                    "type": "data_profile_report",
                    "params": {"title": "Incoming metrics"},
                    "position": {"x": 840, "y": -100},
                },
                {
                    "id": "gate",
                    "type": "code",
                    "params": {
                        "code": "summary = input or {}\n"
                        "if summary.get('n_numeric', 0) != summary.get('n_rows', "
                        "0):\n"
                        "    raise ValueError('quality gate failed: non-numeric "
                        "or missing values')\n"
                        "if summary.get('n_outliers', 0) > 0:\n"
                        '    raise ValueError(f"quality gate failed: '
                        "{summary['n_outliers']} outliers\")\n"
                        "output = {**summary, 'quality_passed': True}"
                    },
                    "position": {"x": 840, "y": 160},
                },
            ],
            "edges": [
                {"source": "start", "target": "read"},
                {"source": "read", "target": "outliers"},
                {"source": "read", "target": "profile"},
                {"source": "outliers", "target": "gate"},
            ],
        },
    },
    {
        "id": "text_extract_regex",
        "name": "Extract fields from raw text",
        "description": (
            "Pull structured fields out of unstructured text with a regular "
            "expression, then reshape them into records."
        ),
        "tags": ["text", "regex", "parsing", "transform"],
        "prerequisites": [],
        "expected_result": "Structured records extracted from the input text.",
        "permissions": [],
        "credential_free": True,
        "graph": _chain(
            (
                "start",
                "manual_trigger",
                {"data": {"text": "order=A-1001 total=42.50\norder=A-1002 total=17.00"}},
            ),
            (
                "extract",
                "regex_extract",
                {"pattern": r"order=(?P<order>[A-Z]-\d+) total=(?P<total>[\d.]+)"},
            ),
            (
                "shape",
                "code",
                {
                    # regex_extract is re.findall, so a two-group pattern yields tuples,
                    # not dicts — named groups included. The original code called .get()
                    # on a tuple, so every run of this template died with AttributeError.
                    "code": (
                        "matches = input if isinstance(input, list) else [input]\n"
                        "output = [{'order': m[0], 'total': float(m[1] or 0)}\n"
                        "          for m in matches if m]"
                    )
                },
            ),
        ),
    },
    {
        "id": "pdf_text_index",
        "name": "Extract text from a PDF",
        "description": (
            "Read a PDF from disk, extract its text page by page, and emit one "
            "record per page ready for search or summarisation."
        ),
        "tags": ["pdf", "documents", "extraction"],
        "prerequisites": ["A PDF readable by the Nodyra process"],
        "expected_result": "One record per page with its text content.",
        "permissions": ["Filesystem: the PDF path you configure"],
        # Not credential-free: needs a PDF you supply.
        "credential_free": False,
        "graph": _chain(
            ("start", "manual_trigger", {"data": {}}),
            ("extract", "pdf_extract_text_v2", {"path": "data/document.pdf"}),
            (
                "pages",
                "code",
                {
                    "code": (
                        "text = (input or {}).get('text', '') if isinstance(input, dict) "
                        "else str(input or '')\n"
                        "pages = [p for p in text.split('\\f') if p.strip()]\n"
                        "output = [{'page': i + 1, 'text': p.strip()}\n"
                        "          for i, p in enumerate(pages)]"
                    )
                },
            ),
        ),
    },
    {
        "id": "scheduled_dataset_snapshot",
        "name": "Nightly dataset snapshot",
        "description": "On a nightly schedule, fetch the current API dataset and export a dated Excel "
        "snapshot with the UTC snapshot date recorded on every row.",
        "tags": ["schedule", "snapshot", "excel", "etl"],
        "credential_free": False,
        "prerequisites": [
            "Outbound HTTPS to the API you are calling",
            "openpyxl installed in the workflow environment",
        ],
        "expected_result": "A dated Excel artifact per nightly run.",
        "permissions": ["Network: the API URL you configure"],
        "graph": {
            "nodes": [
                {
                    "id": "nightly",
                    "type": "schedule_trigger",
                    "params": {"interval": "days", "cron": "0 2 * * *", "tz": "UTC"},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "fetch",
                    "type": "http_request",
                    "params": {
                        "url": "https://jsonplaceholder.typicode.com/posts",
                        "method": "GET",
                    },
                    "position": {"x": 280, "y": 40},
                },
                {
                    "id": "recent",
                    "type": "code",
                    "params": {
                        "code": "from datetime import datetime, timezone\n"
                        "rows = input.get('body', []) if isinstance(input, dict) "
                        "else (input or [])\n"
                        "stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')\n"
                        "output = [{**row, 'snapshot_date': stamp} for row in "
                        "rows]"
                    },
                    "position": {"x": 560, "y": 40},
                },
                {
                    "id": "snapshot",
                    "type": "write_excel_file",
                    "params": {
                        "sheet_name": "snapshot",
                        "filename": "snapshot-{{ $json[0].snapshot_date }}.xlsx",
                    },
                    "position": {"x": 840, "y": 40},
                },
            ],
            "edges": [
                {"source": "nightly", "target": "fetch"},
                {"source": "fetch", "target": "recent"},
                {"source": "recent", "target": "snapshot"},
            ],
        },
    },
    {
        "id": "conditional_routing",
        "name": "Route records by value",
        "description": (
            "Split incoming records down two paths on a threshold, so high and "
            "low value items get different handling."
        ),
        "tags": ["logic", "routing", "branching", "starter"],
        "prerequisites": [],
        "expected_result": "Records above the threshold take one branch; the rest take the other.",
        "permissions": [],
        "credential_free": True,
        "graph": {
            "nodes": [
                _node(
                    "start",
                    "manual_trigger",
                    {"data": {"amount": 250, "customer": "Acme"}},
                    0,
                ),
                _node(
                    "threshold",
                    "if",
                    {"field": "amount", "operator": "greater than", "value": "100"},
                    280,
                ),
                _node(
                    "high_value",
                    "code",
                    {"code": "output = {**(input or {}), 'route': 'review'}"},
                    560,
                    -40,
                ),
                _node(
                    "standard",
                    "code",
                    {"code": "output = {**(input or {}), 'route': 'auto-approve'}"},
                    560,
                    120,
                ),
            ],
            "edges": [
                {"source": "start", "target": "threshold"},
                {"source": "threshold", "target": "high_value", "source_output": "true"},
                {"source": "threshold", "target": "standard", "source_output": "false"},
            ],
        },
    },
    {
        "id": "error_handler_workflow",
        "name": "Catch failures from other workflows",
        "description": "A dedicated error workflow: attach it to any workflow and it receives the failure "
        "details so you can route them one place.",
        "tags": ["errors", "operations", "reliability"],
        "credential_free": True,
        "prerequisites": ["Set this workflow as another workflow's error handler"],
        "expected_result": "One structured failure record per upstream failure.",
        "permissions": [],
        "graph": {
            "nodes": [
                {
                    "id": "failure",
                    "type": "error_trigger",
                    "params": {},
                    "position": {"x": 0, "y": 40},
                },
                {
                    "id": "summarise",
                    "type": "code",
                    "params": {
                        "code": "detail = input or {}\n"
                        "output = {\n"
                        "    'workflow': detail.get('workflow_name') or "
                        "detail.get('workflow_id'),\n"
                        "    'run_id': detail.get('run_id'),\n"
                        "    'node': detail.get('failed_node_id'),\n"
                        "    'error': str(detail.get('error', ''))[:500],\n"
                        "}"
                    },
                    "position": {"x": 280, "y": 40},
                },
            ],
            "edges": [{"source": "failure", "target": "summarise"}],
        },
    },
]


def build() -> list[str]:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for spec in TEMPLATES:
        slug = spec["id"].replace("_", "-")
        preview = f"/template-previews/{slug}.svg"
        template = {
            "id": spec["id"],
            "name": spec["name"],
            "description": spec["description"],
            "tags": spec["tags"],
            "version": "1.0.0",
            "creator": CREATOR,
            "verified": True,
            "credential_free": spec["credential_free"],
            "prerequisites": spec["prerequisites"],
            "expected_result": spec["expected_result"],
            "permissions": spec["permissions"],
            "compatibility": COMPATIBILITY,
            "screenshot_url": preview,
            "graph": spec["graph"],
        }
        (PREVIEW_DIR / f"{slug}.svg").write_text(preview_svg(template), encoding="utf-8")
        path = TEMPLATE_DIR / f"{spec['id']}.json"
        path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
        written.append(path.name)
    return written


if __name__ == "__main__":
    names = build()
    print(f"Wrote {len(names)} templates: {', '.join(names)}")
