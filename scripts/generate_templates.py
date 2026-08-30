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

COMPATIBILITY = ">=0.1.0,<0.2.0"
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
    edges = [
        {"source": specs[i][0], "target": specs[i + 1][0]} for i in range(len(specs) - 1)
    ]
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
        "description": (
            "Poll an endpoint on a schedule, and fail the run loudly when it "
            "stops returning 200 so your alerting sees it."
        ),
        "tags": ["monitoring", "http", "schedule", "starter"],
        "prerequisites": ["Outbound HTTPS to the endpoint you are checking"],
        "expected_result": (
            "A green run each time the endpoint is healthy, and a failed run "
            "carrying the status code when it is not."
        ),
        "permissions": ["Network: the URL you configure"],
        "credential_free": True,
        "graph": _chain(
            ("every_5_min", "schedule_trigger", {"interval": "minutes", "every": 5}),
            (
                "probe",
                "http_request",
                {
                    "url": "https://httpbin.org/status/200",
                    "method": "GET",
                    "timeout_seconds": 10,
                    "max_retries": 1,
                },
            ),
            (
                "check",
                "code",
                {
                    "code": (
                        "status = (input or {}).get('status_code', 0)\n"
                        "if status != 200:\n"
                        "    raise ValueError(f'health check failed: HTTP {status}')\n"
                        "output = {'status_code': status, 'healthy': True}"
                    )
                },
            ),
        ),
    },
    {
        "id": "csv_clean_dedupe",
        "name": "Clean and de-duplicate a CSV",
        "description": (
            "Read a CSV, normalise its text columns, drop duplicate rows by a "
            "key column, and write the cleaned file back out."
        ),
        "tags": ["data", "csv", "cleanup", "starter"],
        "prerequisites": ["A CSV file readable by the Nodyra process"],
        "expected_result": "A cleaned CSV artifact with duplicate rows removed.",
        "permissions": ["Filesystem: the input path you configure"],
        "credential_free": True,
        "graph": _chain(
            ("start", "manual_trigger", {"data": {}}),
            ("read", "read_csv_file", {"path": "data/input.csv", "has_header": True}),
            (
                "normalise",
                "string_normalize",
                {"columns": "email", "case": "lower", "trim": True},
            ),
            ("dedupe", "remove_duplicates", {"field": "email"}),
            ("write", "csv_write", {"filename": "cleaned.csv", "include_header": True}),
        ),
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
        "description": (
            "Fetch a JSON list from an API, pick the fields you care about, "
            "sort it, and export a CSV artifact."
        ),
        "tags": ["api", "csv", "export", "etl"],
        "prerequisites": ["Outbound HTTPS to the API you are calling"],
        "expected_result": "A CSV artifact containing the selected fields, sorted.",
        "permissions": ["Network: the API URL you configure"],
        "credential_free": True,
        "graph": _chain(
            ("start", "manual_trigger", {"data": {}}),
            (
                "fetch",
                "http_request",
                {"url": "https://jsonplaceholder.typicode.com/users", "method": "GET"},
            ),
            (
                "select",
                "code",
                {
                    "code": (
                        "rows = (input or {}).get('body') or input or []\n"
                        "output = [{'id': r.get('id'), 'name': r.get('name'),\n"
                        "           'email': r.get('email')} for r in rows]"
                    )
                },
            ),
            ("sort_rows", "sort", {"field": "name", "order": "asc"}),
            ("export", "csv_write", {"filename": "users.csv", "include_header": True}),
        ),
    },
    {
        "id": "webhook_validate_respond",
        "name": "Validate a webhook payload and reply",
        "description": (
            "Accept an inbound webhook, validate it against a JSON schema, and "
            "answer 200 or 422 so the caller learns immediately what was wrong."
        ),
        "tags": ["webhook", "validation", "api"],
        "prerequisites": ["An inbound webhook URL reachable by the caller"],
        "expected_result": (
            "Valid payloads return 200; invalid ones return the validation "
            "errors rather than failing silently."
        ),
        "permissions": ["Inbound: webhook"],
        "credential_free": True,
        "graph": {
            "nodes": [
                _node(
                    "hook",
                    "webhook_trigger",
                    {"path": "validated-intake", "response_mode": "Last Node"},
                    0,
                ),
                _node(
                    "validate",
                    "schema_validate",
                    {
                        "schema_json": json.dumps(
                            {
                                "type": "object",
                                "required": ["email", "amount"],
                                "properties": {
                                    "email": {"type": "string"},
                                    "amount": {"type": "number"},
                                },
                            }
                        ),
                        "on_error": "split",
                    },
                    280,
                ),
                _node("accepted", "no_op", {}, 560, -40),
                _node(
                    "rejected",
                    "code",
                    {
                        "code": (
                            "output = {'accepted': False,\n"
                            "          'errors': input if input is not None else []}"
                        )
                    },
                    560,
                    120,
                ),
            ],
            "edges": [
                {"source": "hook", "target": "validate"},
                {"source": "validate", "target": "accepted", "sourceHandle": "main"},
                {"source": "validate", "target": "rejected", "sourceHandle": "invalid"},
            ],
        },
    },
    {
        "id": "batch_http_fetch",
        "name": "Fetch many URLs in parallel",
        "description": (
            "Loop over a list of URLs, fetch each one concurrently, and collect "
            "the successes and failures separately."
        ),
        "tags": ["http", "loop", "batch", "concurrency"],
        "prerequisites": ["Outbound HTTPS to the URLs you supply"],
        "expected_result": (
            "One results collection with every successful response, and a "
            "separate errors collection for the ones that failed."
        ),
        "permissions": ["Network: the URLs you supply at run time"],
        "credential_free": True,
        "graph": {
            "nodes": [
                _node(
                    "start",
                    "manual_trigger",
                    {
                        "data": {
                            "urls": [
                                "https://httpbin.org/json",
                                "https://httpbin.org/uuid",
                            ]
                        }
                    },
                    0,
                ),
                _node(
                    "expand",
                    "code",
                    {"code": "output = (input or {}).get('urls', [])"},
                    280,
                ),
                _node(
                    "each",
                    "loop_start",
                    {"mode": "items", "concurrency": 4, "on_error": "continue"},
                    560,
                ),
                _node(
                    "fetch",
                    "http_request",
                    {"url": "{{ $json }}", "method": "GET", "timeout_seconds": 10},
                    840,
                ),
                _node(
                    "collect",
                    "loop_end",
                    {"loop_start_id": "each", "output_mode": "collect"},
                    1120,
                ),
            ],
            "edges": [
                {"source": "start", "target": "expand"},
                {"source": "expand", "target": "each"},
                {"source": "each", "target": "fetch", "sourceHandle": "item"},
                {"source": "fetch", "target": "collect"},
            ],
        },
    },
    {
        "id": "data_quality_gate",
        "name": "Data quality gate",
        "description": (
            "Profile an incoming dataset, flag statistical outliers, and stop "
            "the run before bad data reaches anything downstream."
        ),
        "tags": ["data-quality", "validation", "etl"],
        "prerequisites": ["A CSV file readable by the Nodyra process"],
        "expected_result": (
            "A profile report plus a separate outliers collection; the run "
            "fails when outliers exceed your threshold."
        ),
        "permissions": ["Filesystem: the input path you configure"],
        "credential_free": True,
        "graph": _chain(
            ("start", "manual_trigger", {"data": {}}),
            ("read", "read_csv_file", {"path": "data/metrics.csv", "has_header": True}),
            (
                "outliers",
                "outlier_detect_statistical",
                {"column": "value", "method": "zscore", "threshold": 3.0},
            ),
            ("profile", "data_profile_report", {"title": "Incoming metrics"}),
        ),
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
                {
                    "data": {
                        "text": "order=A-1001 total=42.50\norder=A-1002 total=17.00"
                    }
                },
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
                            "output = [{\'order\': m[0], \'total\': float(m[1] or 0)}\n"
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
        "credential_free": True,
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
        "description": (
            "On a nightly schedule, pull a dataset from an API, keep only the "
            "rows that changed, and write a dated Excel snapshot."
        ),
        "tags": ["schedule", "snapshot", "excel", "etl"],
        "prerequisites": ["Outbound HTTPS to the API you are calling"],
        "expected_result": "A dated Excel artifact per nightly run.",
        "permissions": ["Network: the API URL you configure"],
        "credential_free": True,
        "graph": _chain(
            ("nightly", "schedule_trigger", {"interval": "cron", "cron": "0 2 * * *"}),
            (
                "fetch",
                "http_request",
                {"url": "https://jsonplaceholder.typicode.com/posts", "method": "GET"},
            ),
            (
                "recent",
                "code",
                {
                    "code": (
                        "rows = (input or {}).get('body') or input or []\n"
                        "output = rows[:50]"
                    )
                },
            ),
            ("snapshot", "write_excel_file", {"sheet_name": "snapshot"}),
        ),
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
                    {"field": "amount", "operator": "gt", "value": "100"},
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
                {"source": "threshold", "target": "high_value", "sourceHandle": "true"},
                {"source": "threshold", "target": "standard", "sourceHandle": "false"},
            ],
        },
    },
    {
        "id": "error_handler_workflow",
        "name": "Catch failures from other workflows",
        "description": (
            "A dedicated error workflow: attach it to any workflow and it "
            "receives the failure details so you can route them one place."
        ),
        "tags": ["errors", "operations", "reliability"],
        "prerequisites": ["Set this workflow as another workflow's error handler"],
        "expected_result": "One structured failure record per upstream failure.",
        "permissions": [],
        "credential_free": True,
        "graph": _chain(
            ("failure", "error_trigger", {}),
            (
                "summarise",
                "code",
                {
                    "code": (
                        "detail = input or {}\n"
                        "output = {\n"
                        "    'workflow': detail.get('workflow_name') "
                        "or detail.get('workflow_id'),\n"
                        "    'run_id': detail.get('run_id'),\n"
                        "    'node': detail.get('node_id'),\n"
                        "    'error': str(detail.get('error', ''))[:500],\n"
                        "}"
                    )
                },
            ),
        ),
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
