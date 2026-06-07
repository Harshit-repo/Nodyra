# Browser & Web Automation Nodes — Wave 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 7 browser and web automation nodes (headless browser via Playwright, HTML extraction, RSS/Atom feed parsing, GraphQL requests) that have no equivalent in n8n.

**Architecture:** All nodes live in a new `packages/nodes/noodle_nodes/browser_automation.py` module, registered in `__init__.py`. The four Playwright nodes (`browser_screenshot`, `browser_scrape`, `browser_click_fill`, `browser_pdf_from_url`) lazy-import `playwright.sync_api.sync_playwright` inside each function body and are all marked `tool_side_effecting=True`. Three lighter nodes (`html_extract`, `web_feed_parse`, `graphql_request`) use `beautifulsoup4`, `feedparser`, and `requests` respectively — also lazy-imported. No optional package is imported at module scope. Tests mock all optional packages via `patch.dict(sys.modules, ...)` so no heavy package install is needed in CI.

**Tech Stack:** `playwright>=1.40`, `beautifulsoup4>=4.12`, `feedparser>=6.0`, `requests>=2.28`

**Spec:** `docs/superpowers/specs/2026-06-05-python-powered-unique-nodes-design.md` (Category 2 — Browser & Web Automation)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `packages/nodes/noodle_nodes/browser_automation.py` | All 7 nodes + private helpers |
| Modify | `packages/nodes/noodle_nodes/__init__.py` | Import + register the new module |
| Create | `packages/nodes/tests/test_browser_automation.py` | All tests |

**Implementation order:** non-browser nodes first (Tasks 2–4 test without Playwright installed), then browser nodes (Tasks 5–8).

---

## Task 1: Module scaffold + import-safety test

**Files:**
- Create: `packages/nodes/noodle_nodes/browser_automation.py`
- Create: `packages/nodes/tests/test_browser_automation.py`
- Modify: `packages/nodes/noodle_nodes/__init__.py`

- [ ] **Step 1: Write the failing import-safety test**

```python
# packages/nodes/tests/test_browser_automation.py
"""Tests for browser and web automation nodes."""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 — registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="browser-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("browser-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def _make_pw_mocks():
    """Return (mock_sync_playwright_fn, mock_page, mock_pw_sync_api_module).

    Builds a full Playwright sync-API mock hierarchy:
      sync_playwright() -> context mgr -> playwright obj
      playwright.chromium.launch() -> browser
      browser.new_page() -> page
    """
    mock_page = MagicMock()
    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw_obj = MagicMock()
    mock_pw_obj.chromium.launch.return_value = mock_browser
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_pw_obj)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    mock_sync_playwright = MagicMock(return_value=mock_ctx)
    mock_pw_sync_api = MagicMock()
    mock_pw_sync_api.sync_playwright = mock_sync_playwright
    mock_pw_sync_api.TimeoutError = TimeoutError  # stand-in for playwright.sync_api.TimeoutError
    return mock_sync_playwright, mock_page, mock_pw_sync_api


def test_browser_automation_importable_without_optional_packages() -> None:
    """Module must import cleanly even when no browser packages are installed."""
    import importlib
    mod = importlib.import_module("noodle_nodes.browser_automation")
    assert hasattr(mod, "html_extract")
    assert hasattr(mod, "web_feed_parse")
    assert hasattr(mod, "graphql_request")
    assert hasattr(mod, "browser_screenshot")
    assert hasattr(mod, "browser_scrape")
    assert hasattr(mod, "browser_click_fill")
    assert hasattr(mod, "browser_pdf_from_url")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py::test_browser_automation_importable_without_optional_packages -v
```

Expected: `ModuleNotFoundError: No module named 'noodle_nodes.browser_automation'`

- [ ] **Step 3: Create the module scaffold**

```python
# packages/nodes/noodle_nodes/browser_automation.py
"""Browser and web automation nodes — Playwright, HTML extraction, RSS, GraphQL.

All heavy dependencies are lazy-imported inside each function body.
No optional package is imported at module scope.
"""

from __future__ import annotations

import io
import json
from typing import Any

from noodle.artifacts import read_bytes as _read_bytes
from noodle.artifacts import write_bytes as _write_bytes
from noodle.sdk import node

# Nodes are defined below.
```

- [ ] **Step 4: Register in `__init__.py`**

Open `packages/nodes/noodle_nodes/__init__.py`. After the `document_intelligence` import line add:

```python
from noodle_nodes import browser_automation as browser_automation
```

In `__all__`, add `"browser_automation"` after `"document_intelligence"`.

- [ ] **Step 5: Run import-safety test — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py::test_browser_automation_importable_without_optional_packages -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/noodle_nodes/__init__.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): scaffold browser_automation module"
```

---

## Task 2: `html_extract`

Extracts structured data from an HTML string using CSS selectors. A `container_selector` defines the repeating element (e.g. `li.product`); `selectors_json` maps field names to CSS selectors *within* each container. If no container is given, one record is returned using page-level selectors (first match per field).

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

HTML_SAMPLE = """
<html><body>
  <ul>
    <li class="product"><h2>Widget A</h2><span class="price">$10</span></li>
    <li class="product"><h2>Widget B</h2><span class="price">$20</span></li>
  </ul>
  <p id="desc">Best widgets ever.</p>
</body></html>
"""


def test_html_extract_raises_without_input(store_ctx) -> None:
    from noodle_nodes.browser_automation import html_extract
    with pytest.raises(ValueError, match="input is required"):
        html_extract(input=None)


def test_html_extract_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "bs4":
            raise ImportError("No module named 'bs4'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import html_extract
    with pytest.raises(RuntimeError, match="beautifulsoup4"):
        html_extract(input=HTML_SAMPLE)


def test_html_extract_container_returns_records(store_ctx) -> None:
    pytest.importorskip("bs4")
    from noodle_nodes.browser_automation import html_extract

    result = html_extract(
        input=HTML_SAMPLE,
        container_selector="li.product",
        selectors_json='{"name": "h2", "price": ".price"}',
    )
    assert is_dataset_ref(result["dataset"])
    assert result["records_found"] == 2
    assert result["records"][0]["name"] == "Widget A"
    assert result["records"][0]["price"] == "$10"
    assert result["records"][1]["name"] == "Widget B"


def test_html_extract_no_container_single_record(store_ctx) -> None:
    pytest.importorskip("bs4")
    from noodle_nodes.browser_automation import html_extract

    result = html_extract(
        input=HTML_SAMPLE,
        selectors_json='{"description": "#desc"}',
    )
    assert result["records_found"] == 1
    assert result["records"][0]["description"] == "Best widgets ever."


def test_html_extract_missing_selector_returns_empty_string(store_ctx) -> None:
    pytest.importorskip("bs4")
    from noodle_nodes.browser_automation import html_extract

    result = html_extract(
        input=HTML_SAMPLE,
        container_selector="li.product",
        selectors_json='{"name": "h2", "missing": ".no-such-class"}',
    )
    assert result["records"][0]["missing"] == ""
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "html_extract" -v
```

Expected: 5 failures (function not yet defined)

- [ ] **Step 3: Implement `html_extract`**

```python
@node(
    name="HTML Extract",
    id="html_extract",
    category="Browser & Web",
    icon="code",
    requirements=["beautifulsoup4>=4.12"],
    params={
        "container_selector": {
            "placeholder": "li.product  (blank = whole page, one record)",
            "description": "CSS selector for the repeating parent element. Each match becomes one record.",
        },
        "selectors_json": {
            "multiline": True,
            "placeholder": '{"title": "h2", "price": ".price", "link": "a"}',
            "description": "JSON mapping of field name → CSS selector (relative to container, or page if no container).",
        },
    },
)
def html_extract(
    input=None,
    container_selector: str = "",
    selectors_json: str = "{}",
) -> dict:
    """Extract structured records from an HTML string using CSS selectors."""
    if input is None:
        raise ValueError("input is required — wire an HTML string or artifact to this node.")

    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "beautifulsoup4 is required. Add beautifulsoup4 to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from noodle_nodes.datasets import records_to_dataset

    if isinstance(input, (bytes, bytearray)):
        html = input.decode("utf-8", errors="replace")
    elif is_artifact_ref := _is_artifact_ref(input):
        html = _read_bytes(input).decode("utf-8", errors="replace")
    else:
        html = str(input)

    try:
        field_selectors: dict[str, str] = json.loads(selectors_json) if selectors_json.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"selectors_json must be valid JSON: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")

    def _text(el: Any) -> str:
        return el.get_text(strip=True) if el else ""

    records: list[dict[str, Any]] = []

    if container_selector.strip():
        containers = soup.select(container_selector)
        for container in containers:
            record = {
                field: _text(container.select_one(sel))
                for field, sel in field_selectors.items()
            }
            records.append(record)
    else:
        record = {
            field: _text(soup.select_one(sel))
            for field, sel in field_selectors.items()
        }
        records.append(record)

    dataset = records_to_dataset(records) if records else None
    return {
        "records": records,
        "records_found": len(records),
        "dataset": dataset,
    }


def _is_artifact_ref(value: Any) -> bool:
    """Return True when value looks like an ArtifactRef dict."""
    return isinstance(value, dict) and "artifact_id" in value
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "html_extract" -v
```

Expected: 5 PASSED (skipped if beautifulsoup4 not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add html_extract node"
```

---

## Task 3: `web_feed_parse`

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Post One</title>
      <link>https://example.com/1</link>
      <description>First post summary.</description>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Post Two</title>
      <link>https://example.com/2</link>
      <description>Second post summary.</description>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def test_web_feed_parse_raises_without_input(store_ctx) -> None:
    from noodle_nodes.browser_automation import web_feed_parse
    with pytest.raises(ValueError, match="input is required"):
        web_feed_parse(input=None)


def test_web_feed_parse_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "feedparser":
            raise ImportError("No module named 'feedparser'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import web_feed_parse
    with pytest.raises(RuntimeError, match="feedparser"):
        web_feed_parse(input="https://example.com/feed.xml")


def test_web_feed_parse_parses_rss_string(store_ctx) -> None:
    pytest.importorskip("feedparser")
    from noodle_nodes.browser_automation import web_feed_parse

    result = web_feed_parse(input=RSS_XML)
    assert result["entry_count"] == 2
    assert is_dataset_ref(result["dataset"])
    assert result["feed_title"] == "Test Feed"
    assert result["entries"][0]["title"] == "Post One"
    assert result["entries"][0]["link"] == "https://example.com/1"


def test_web_feed_parse_respects_max_items(store_ctx) -> None:
    pytest.importorskip("feedparser")
    from noodle_nodes.browser_automation import web_feed_parse

    result = web_feed_parse(input=RSS_XML, max_items=1)
    assert result["entry_count"] == 1
    assert result["entries"][0]["title"] == "Post One"


def test_web_feed_parse_raises_on_invalid_feed(store_ctx) -> None:
    pytest.importorskip("feedparser")
    from noodle_nodes.browser_automation import web_feed_parse

    with pytest.raises(ValueError, match="No feed entries"):
        web_feed_parse(input="<html><body>not a feed</body></html>")
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "web_feed_parse" -v
```

Expected: 5 failures

- [ ] **Step 3: Implement `web_feed_parse`**

```python
@node(
    name="RSS / Atom Feed Parse",
    id="web_feed_parse",
    category="Browser & Web",
    icon="rss",
    requirements=["feedparser>=6.0"],
    params={
        "max_items": {"description": "Maximum feed entries to return (0 = all, default 50)"},
    },
)
def web_feed_parse(input=None, max_items: int = 50) -> dict:
    """Parse an RSS or Atom feed URL or XML string; return entries as a DatasetRef."""
    if input is None:
        raise ValueError("input is required — wire a feed URL or XML string to this node.")

    try:
        import feedparser  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "feedparser is required. Add feedparser to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    from noodle_nodes.datasets import records_to_dataset

    source = str(input) if not isinstance(input, str) else input
    parsed = feedparser.parse(source)

    raw_entries = parsed.get("entries", [])
    if not raw_entries and not parsed.get("feed", {}).get("title"):
        raise ValueError(
            "No feed entries found. Check that the input is a valid RSS or Atom feed URL or XML. "
            f"feedparser bozo exception: {parsed.get('bozo_exception')}"
        )

    limit = len(raw_entries) if max_items == 0 else min(max_items, len(raw_entries))
    entries = []
    for e in raw_entries[:limit]:
        entries.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "summary": e.get("summary", ""),
            "published": e.get("published", ""),
            "author": e.get("author", ""),
            "id": e.get("id", ""),
        })

    feed_meta = parsed.get("feed", {})
    dataset = records_to_dataset(entries) if entries else None
    return {
        "entries": entries,
        "entry_count": len(entries),
        "total_available": len(raw_entries),
        "feed_title": feed_meta.get("title", ""),
        "feed_link": feed_meta.get("link", ""),
        "dataset": dataset,
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "web_feed_parse" -v
```

Expected: 5 PASSED (skipped if feedparser not installed)

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add web_feed_parse node"
```

---

## Task 4: `graphql_request`

Takes a query (string or dict with `query`/`variables` keys) and a GraphQL endpoint URL, executes a POST, and returns the response `data`. Marked `tool_side_effecting=True` because mutations write data.

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

def test_graphql_request_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import graphql_request
    with pytest.raises(ValueError, match="url is required"):
        graphql_request(input="{ users { id } }", url="")


def test_graphql_request_raises_without_query(store_ctx) -> None:
    from noodle_nodes.browser_automation import graphql_request
    with pytest.raises(ValueError, match="query is required"):
        graphql_request(input=None, url="https://api.example.com/graphql")


def test_graphql_request_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("No module named 'requests'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import graphql_request
    with pytest.raises(RuntimeError, match="requests"):
        graphql_request(input="{ users { id } }", url="https://api.example.com/graphql")


def test_graphql_request_posts_query_and_returns_data(store_ctx) -> None:
    pytest.importorskip("requests")
    from unittest.mock import MagicMock, patch
    from noodle_nodes.browser_automation import graphql_request

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"users": [{"id": "1", "name": "Alice"}]}}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = graphql_request(
            input="{ users { id name } }",
            url="https://api.example.com/graphql",
        )

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "https://api.example.com/graphql"
    payload = call_kwargs[1]["json"]
    assert payload["query"] == "{ users { id name } }"
    assert result["data"] == {"users": [{"id": "1", "name": "Alice"}]}
    assert result["errors"] is None
    assert result["status_code"] == 200


def test_graphql_request_accepts_dict_input_with_variables(store_ctx) -> None:
    pytest.importorskip("requests")
    from unittest.mock import MagicMock, patch
    from noodle_nodes.browser_automation import graphql_request

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"user": {"id": "42"}}}
    mock_resp.raise_for_status = MagicMock()

    gql_input = {"query": "query GetUser($id: ID!) { user(id: $id) { id } }", "variables": {"id": "42"}}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = graphql_request(input=gql_input, url="https://api.example.com/graphql")

    payload = mock_post.call_args[1]["json"]
    assert payload["variables"] == {"id": "42"}
    assert result["data"] == {"user": {"id": "42"}}


def test_graphql_request_surfaces_graphql_errors(store_ctx) -> None:
    pytest.importorskip("requests")
    from unittest.mock import MagicMock, patch
    from noodle_nodes.browser_automation import graphql_request

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": None,
        "errors": [{"message": "Field 'foo' doesn't exist"}],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_resp):
        result = graphql_request(input="{ foo }", url="https://api.example.com/graphql")

    assert result["errors"] == [{"message": "Field 'foo' doesn't exist"}]
    assert result["data"] is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "graphql_request" -v
```

Expected: 6 failures

- [ ] **Step 3: Implement `graphql_request`**

```python
@node(
    name="GraphQL Request",
    id="graphql_request",
    category="Browser & Web",
    icon="code",
    tool_side_effecting=True,
    requirements=["requests>=2.28"],
    params={
        "url": {"placeholder": "https://api.example.com/graphql"},
        "headers_json": {
            "multiline": True,
            "placeholder": '{"Authorization": "Bearer YOUR_TOKEN"}',
            "description": "JSON object of HTTP headers to include.",
        },
        "timeout_seconds": {"description": "Request timeout in seconds (default 30)"},
    },
)
def graphql_request(
    input=None,
    url: str = "",
    headers_json: str = "{}",
    timeout_seconds: int = 30,
) -> dict:
    """Execute a GraphQL query or mutation and return the response data.

    Input can be a query string or a dict with 'query' and optional 'variables' keys.
    """
    if not url:
        raise ValueError("url is required — set the GraphQL endpoint in the node config.")

    query: str
    variables: dict[str, Any] = {}
    operation_name: str | None = None

    if input is None:
        raise ValueError(
            "query is required — wire a query string or {query, variables} dict as input."
        )
    elif isinstance(input, dict):
        query = input.get("query", "")
        variables = input.get("variables", {}) or {}
        operation_name = input.get("operationName")
    else:
        query = str(input)

    if not query.strip():
        raise ValueError(
            "query is required — wire a query string or {query, variables} dict as input."
        )

    try:
        import requests as _requests  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "requests is required. Add requests to the workflow "
            "environment, rebuild it, then run again."
        ) from exc

    try:
        headers: dict[str, str] = json.loads(headers_json) if headers_json.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"headers_json must be valid JSON: {exc}") from exc

    headers.setdefault("Content-Type", "application/json")

    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    if operation_name:
        payload["operationName"] = operation_name

    try:
        resp = _requests.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        resp.raise_for_status()
    except _requests.exceptions.Timeout as exc:
        raise RuntimeError(
            f"GraphQL request timed out after {timeout_seconds}s. "
            "Increase timeout_seconds or check endpoint availability."
        ) from exc
    except _requests.exceptions.RequestException as exc:
        raise RuntimeError(f"GraphQL request failed: {exc}") from exc

    body: dict[str, Any] = resp.json()
    return {
        "data": body.get("data"),
        "errors": body.get("errors"),
        "status_code": resp.status_code,
        "extensions": body.get("extensions"),
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "graphql_request" -v
```

Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add graphql_request node"
```

---

## Task 5: `browser_screenshot` ★

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

**Playwright mock pattern used in all browser tests:**

```python
mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
with patch.dict(sys.modules, {
    "playwright": MagicMock(),
    "playwright.sync_api": mock_pw_sync_api,
}):
    from noodle_nodes.browser_automation import browser_screenshot
    result = browser_screenshot(url="https://example.com")
```

The `from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout` inside the function will resolve to the mocked module at call time.

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # minimal fake PNG header


def test_browser_screenshot_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_screenshot
    with pytest.raises(ValueError, match="url is required"):
        browser_screenshot(input=None, url="")


def test_browser_screenshot_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "playwright" or name == "playwright.sync_api":
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import browser_screenshot
    with pytest.raises(RuntimeError, match="playwright"):
        browser_screenshot(url="https://example.com")


def test_browser_screenshot_returns_png_artifact(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.screenshot.return_value = FAKE_PNG

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(url="https://example.com")

    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "image/png"
    assert result["url"] == "https://example.com"
    assert result["size_bytes"] == len(FAKE_PNG)


def test_browser_screenshot_uses_url_from_input(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.screenshot.return_value = FAKE_PNG

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(input="https://from-input.com")

    assert result["url"] == "https://from-input.com"


def test_browser_screenshot_selector_mode(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_element = MagicMock()
    mock_element.screenshot.return_value = FAKE_PNG
    mock_page.query_selector.return_value = mock_element

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(url="https://example.com", selector="#hero")

    mock_page.query_selector.assert_called_with("#hero")
    mock_element.screenshot.assert_called_once()
    assert is_artifact_ref(result["artifact"])


def test_browser_screenshot_raises_when_selector_not_found(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.query_selector.return_value = None

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_screenshot
        with pytest.raises(ValueError, match="matched no elements"):
            browser_screenshot(url="https://example.com", selector=".no-such-element")


def test_browser_screenshot_timeout_raises_runtime_error(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.goto.side_effect = TimeoutError("Timeout")

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_screenshot
        with pytest.raises(RuntimeError, match="timed out"):
            browser_screenshot(url="https://example.com", page_timeout_ms=5000)
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_screenshot" -v
```

Expected: 7 failures

- [ ] **Step 3: Implement `browser_screenshot`**

```python
@node(
    name="Browser Screenshot",
    id="browser_screenshot",
    category="Browser & Web",
    icon="camera",
    tool_side_effecting=True,
    requirements=["playwright>=1.40"],
    params={
        "url": {"placeholder": "https://example.com"},
        "selector": {
            "placeholder": "#hero  (blank = full page)",
            "description": "CSS selector to screenshot a specific element. Leave blank to capture the full page.",
        },
        "full_page": {"description": "Capture the full scrollable page (ignored when selector is set)"},
        "viewport_width": {"description": "Browser viewport width in pixels (default 1280)"},
        "viewport_height": {"description": "Browser viewport height in pixels (default 800)"},
        "wait_for": {
            "choices": ["networkidle", "load", "domcontentloaded"],
            "description": "Wait condition before capturing. networkidle waits for no network activity.",
        },
        "page_timeout_ms": {"description": "Maximum page load wait in ms (default 30000)"},
        "filename": {"placeholder": "screenshot.png"},
    },
)
def browser_screenshot(
    input=None,
    url: str = "",
    selector: str = "",
    full_page: bool = False,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    wait_for: str = "networkidle",
    page_timeout_ms: int = 30_000,
    filename: str = "screenshot.png",
) -> dict:
    """Screenshot a URL or CSS selector to an image artifact using a headless Chromium browser.

    Requires: playwright install chromium  (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError("url is required — set a URL in the node config or wire one as input.")

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required. Add playwright to the workflow environment, "
            "rebuild it, then run 'playwright install chromium' inside the environment."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": viewport_width, "height": viewport_height}
            )
            try:
                page.goto(target_url, wait_until=wait_for, timeout=page_timeout_ms)
            except (PWTimeout, TimeoutError) as exc:
                raise RuntimeError(
                    f"Page timed out after {page_timeout_ms}ms loading {target_url}. "
                    "Increase page_timeout_ms or check the URL is reachable from the runner."
                ) from exc

            if selector.strip():
                element = page.query_selector(selector)
                if element is None:
                    raise ValueError(
                        f"Selector '{selector}' matched no elements on {target_url}."
                    )
                img_bytes: bytes = element.screenshot()
            else:
                img_bytes = page.screenshot(full_page=full_page)
        finally:
            browser.close()

    artifact = _write_bytes(
        img_bytes,
        name=filename or "screenshot.png",
        content_type="image/png",
        kind="image",
    )
    return {"artifact": artifact, "url": target_url, "size_bytes": len(img_bytes)}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_screenshot" -v
```

Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add browser_screenshot node (killer node)"
```

---

## Task 6: `browser_scrape` ★

Navigates to a URL, waits for the page to load, and extracts structured records using a `container_selector` + field `selectors_json`. Supports multi-page pagination via a `pagination_next_selector`.

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

def test_browser_scrape_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_scrape
    with pytest.raises(ValueError, match="url is required"):
        browser_scrape(input=None, url="")


def test_browser_scrape_raises_without_selectors(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_scrape
        with pytest.raises(ValueError, match="selectors_json"):
            browser_scrape(url="https://example.com", selectors_json="")


def test_browser_scrape_extracts_records_with_container(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()

    mock_el_a = MagicMock()
    mock_el_a.query_selector.side_effect = lambda sel: (
        MagicMock(text_content=MagicMock(return_value="Widget A")) if sel == "h2"
        else MagicMock(text_content=MagicMock(return_value="$10"))
    )
    mock_el_b = MagicMock()
    mock_el_b.query_selector.side_effect = lambda sel: (
        MagicMock(text_content=MagicMock(return_value="Widget B")) if sel == "h2"
        else MagicMock(text_content=MagicMock(return_value="$20"))
    )
    mock_page.query_selector_all.return_value = [mock_el_a, mock_el_b]
    mock_page.query_selector.return_value = None  # no pagination

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_scrape
        result = browser_scrape(
            url="https://shop.example.com",
            container_selector="li.product",
            selectors_json='{"name": "h2", "price": ".price"}',
        )

    assert result["records_found"] == 2
    assert is_dataset_ref(result["dataset"])
    assert result["pages_scraped"] == 1
    assert result["records"][0]["name"] == "Widget A"


def test_browser_scrape_missing_field_returns_empty_string(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()

    mock_el = MagicMock()
    mock_el.query_selector.return_value = None  # selector matches nothing
    mock_page.query_selector_all.return_value = [mock_el]
    mock_page.query_selector.return_value = None

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_scrape
        result = browser_scrape(
            url="https://example.com",
            container_selector="div.item",
            selectors_json='{"title": "h1"}',
        )

    assert result["records"][0]["title"] == ""


def test_browser_scrape_paginates_to_max_pages(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()

    call_count = {"n": 0}

    def query_selector_all_side_effect(sel):
        if sel == "li.item":
            call_count["n"] += 1
            mock_el = MagicMock()
            mock_el.query_selector.return_value = MagicMock(
                text_content=MagicMock(return_value=f"Item page {call_count['n']}")
            )
            return [mock_el]
        return []

    mock_page.query_selector_all.side_effect = query_selector_all_side_effect
    # Pagination: return a next-link for first call, None for second
    next_link_mock = MagicMock()
    mock_page.query_selector.side_effect = [next_link_mock, None]

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_scrape
        result = browser_scrape(
            url="https://example.com/list",
            container_selector="li.item",
            selectors_json='{"label": "span"}',
            pagination_next_selector="a.next",
            max_pages=5,
        )

    assert result["pages_scraped"] == 2
    assert result["records_found"] == 2
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_scrape" -v
```

Expected: 5 failures

- [ ] **Step 3: Implement `browser_scrape`**

```python
@node(
    name="Browser Scrape",
    id="browser_scrape",
    category="Browser & Web",
    icon="globe",
    tool_side_effecting=True,
    requirements=["playwright>=1.40"],
    params={
        "url": {"placeholder": "https://example.com/products"},
        "container_selector": {
            "placeholder": "li.product-card",
            "description": "CSS selector for the repeating element. Each match becomes one record.",
        },
        "selectors_json": {
            "multiline": True,
            "placeholder": '{"title": "h2.name", "price": ".price", "link": "a[href]"}',
            "description": "JSON mapping of field name → CSS selector (relative to each container).",
        },
        "wait_for": {
            "choices": ["networkidle", "load", "domcontentloaded"],
            "description": "Wait condition before extracting. networkidle is most reliable.",
        },
        "pagination_next_selector": {
            "placeholder": "a.next-page",
            "description": "CSS selector for the 'next page' link. Leave blank to disable pagination.",
        },
        "max_pages": {"description": "Maximum pages to paginate through (default 1)"},
        "page_timeout_ms": {"description": "Maximum wait per page in ms (default 30000)"},
    },
)
def browser_scrape(
    input=None,
    url: str = "",
    container_selector: str = "",
    selectors_json: str = "",
    wait_for: str = "networkidle",
    pagination_next_selector: str = "",
    max_pages: int = 1,
    page_timeout_ms: int = 30_000,
) -> dict:
    """Navigate to a URL and extract structured records using CSS selectors.

    Requires: playwright install chromium  (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError("url is required — set a URL in the node config or wire one as input.")
    if not selectors_json.strip():
        raise ValueError(
            "selectors_json is required — provide a JSON mapping of field name → CSS selector."
        )

    try:
        field_selectors: dict[str, str] = json.loads(selectors_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"selectors_json must be valid JSON: {exc}") from exc

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required. Add playwright to the workflow environment, "
            "rebuild it, then run 'playwright install chromium' inside the environment."
        ) from exc

    from noodle_nodes.datasets import records_to_dataset

    all_records: list[dict[str, Any]] = []
    pages_scraped = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            try:
                page.goto(target_url, wait_until=wait_for, timeout=page_timeout_ms)
            except (PWTimeout, TimeoutError) as exc:
                raise RuntimeError(
                    f"Page timed out after {page_timeout_ms}ms. "
                    "Increase page_timeout_ms or check the URL is reachable."
                ) from exc

            while pages_scraped < max(1, max_pages):
                if container_selector.strip():
                    containers = page.query_selector_all(container_selector)
                    for container in containers:
                        record: dict[str, Any] = {}
                        for field, sel in field_selectors.items():
                            el = container.query_selector(sel)
                            record[field] = el.text_content().strip() if el else ""
                        all_records.append(record)
                else:
                    record = {}
                    for field, sel in field_selectors.items():
                        el = page.query_selector(sel)
                        record[field] = el.text_content().strip() if el else ""
                    all_records.append(record)

                pages_scraped += 1

                if not pagination_next_selector.strip() or pages_scraped >= max_pages:
                    break

                next_link = page.query_selector(pagination_next_selector)
                if next_link is None:
                    break
                next_link.click()
                try:
                    page.wait_for_load_state(wait_for, timeout=page_timeout_ms)
                except (PWTimeout, TimeoutError):
                    break
        finally:
            browser.close()

    dataset = records_to_dataset(all_records) if all_records else None
    return {
        "records": all_records,
        "records_found": len(all_records),
        "pages_scraped": pages_scraped,
        "dataset": dataset,
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_scrape" -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add browser_scrape node (killer node)"
```

---

## Task 7: `browser_click_fill`

Automates a sequence of browser interactions (click, fill, select, wait) on a starting URL. Returns the final page URL, title, and an optional screenshot.

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

CLICK_FILL_ACTIONS = [
    {"action": "fill", "selector": "#username", "value": "alice@example.com"},
    {"action": "fill", "selector": "#password", "value": "secret"},
    {"action": "click", "selector": "button[type=submit]"},
]


def test_browser_click_fill_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_click_fill
    with pytest.raises(ValueError, match="url is required"):
        browser_click_fill(input=None, url="", actions_json="[]")


def test_browser_click_fill_raises_without_actions(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_click_fill
    with pytest.raises(ValueError, match="actions_json is required"):
        browser_click_fill(url="https://example.com", actions_json="")


def test_browser_click_fill_executes_fill_and_click(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.url = "https://example.com/dashboard"
    mock_page.title.return_value = "Dashboard"
    mock_page.screenshot.return_value = FAKE_PNG

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_click_fill
        result = browser_click_fill(
            url="https://example.com/login",
            actions_json=json.dumps(CLICK_FILL_ACTIONS),
            screenshot_after=True,
        )

    mock_page.fill.assert_any_call("#username", "alice@example.com")
    mock_page.fill.assert_any_call("#password", "secret")
    mock_page.click.assert_called_with("button[type=submit]")
    assert result["final_url"] == "https://example.com/dashboard"
    assert result["title"] == "Dashboard"
    assert is_artifact_ref(result["screenshot"])


def test_browser_click_fill_wait_for_selector_action(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.url = "https://example.com/ok"
    mock_page.title.return_value = "OK"
    mock_page.screenshot.return_value = FAKE_PNG

    actions = [{"action": "wait_for_selector", "selector": ".success-banner", "timeout_ms": 3000}]

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_click_fill
        result = browser_click_fill(
            url="https://example.com",
            actions_json=json.dumps(actions),
        )

    mock_page.wait_for_selector.assert_called_with(".success-banner", timeout=3000)
    assert result["final_url"] == "https://example.com/ok"


def test_browser_click_fill_raises_on_unknown_action(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.url = "https://example.com"
    mock_page.title.return_value = "Page"

    actions = [{"action": "unknown_action", "selector": "#x"}]

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_click_fill
        with pytest.raises(ValueError, match="Unknown action"):
            browser_click_fill(url="https://example.com", actions_json=json.dumps(actions))
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_click_fill" -v
```

Expected: 4 failures

- [ ] **Step 3: Implement `browser_click_fill`**

```python
@node(
    name="Browser Click & Fill",
    id="browser_click_fill",
    category="Browser & Web",
    icon="cursor-click",
    tool_side_effecting=True,
    requirements=["playwright>=1.40"],
    params={
        "url": {"placeholder": "https://example.com/login"},
        "actions_json": {
            "multiline": True,
            "placeholder": json.dumps([
                {"action": "fill", "selector": "#username", "value": "user@example.com"},
                {"action": "fill", "selector": "#password", "value": "secret"},
                {"action": "click", "selector": "button[type=submit]"},
                {"action": "wait_for_selector", "selector": ".dashboard", "timeout_ms": 5000},
            ], indent=2),
            "description": (
                "JSON list of browser actions. Each action: "
                "{action: click|fill|select|wait_for_selector|wait, selector?, value?, timeout_ms?, ms?}."
            ),
        },
        "screenshot_after": {"description": "Capture a screenshot of the final page state"},
        "page_timeout_ms": {"description": "Maximum page load wait per navigation in ms (default 30000)"},
        "viewport_width": {"description": "Browser viewport width in pixels (default 1280)"},
        "viewport_height": {"description": "Browser viewport height in pixels (default 800)"},
    },
)
def browser_click_fill(
    input=None,
    url: str = "",
    actions_json: str = "",
    screenshot_after: bool = False,
    page_timeout_ms: int = 30_000,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> dict:
    """Execute a sequence of browser interactions and return the final page state.

    Requires: playwright install chromium  (run once in the environment after installing playwright).

    Supported actions:
      {"action": "click", "selector": "CSS"}
      {"action": "fill", "selector": "CSS", "value": "text"}
      {"action": "select", "selector": "CSS", "value": "option-value"}
      {"action": "wait_for_selector", "selector": "CSS", "timeout_ms": 5000}
      {"action": "wait", "ms": 1000}
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError("url is required — set a starting URL in the node config or wire one as input.")
    if not actions_json.strip():
        raise ValueError(
            "actions_json is required — provide a JSON list of browser actions in the node config."
        )

    try:
        actions: list[dict[str, Any]] = json.loads(actions_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"actions_json must be valid JSON: {exc}") from exc

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required. Add playwright to the workflow environment, "
            "rebuild it, then run 'playwright install chromium' inside the environment."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": viewport_width, "height": viewport_height}
            )
            try:
                page.goto(target_url, wait_until="networkidle", timeout=page_timeout_ms)
            except (PWTimeout, TimeoutError) as exc:
                raise RuntimeError(
                    f"Page timed out after {page_timeout_ms}ms loading {target_url}."
                ) from exc

            for step_idx, action in enumerate(actions):
                act = action.get("action", "")
                sel = action.get("selector", "")
                val = action.get("value", "")
                timeout = int(action.get("timeout_ms", 5_000))

                if act == "click":
                    page.click(sel)
                elif act == "fill":
                    page.fill(sel, str(val))
                elif act == "select":
                    page.select_option(sel, str(val))
                elif act == "wait_for_selector":
                    page.wait_for_selector(sel, timeout=timeout)
                elif act == "wait":
                    import time
                    time.sleep(int(action.get("ms", 1000)) / 1000)
                else:
                    raise ValueError(
                        f"Unknown action '{act}' at step {step_idx}. "
                        "Valid actions: click, fill, select, wait_for_selector, wait."
                    )

            final_url = page.url
            title = page.title()
            screenshot_ref = None
            if screenshot_after:
                screenshot_ref = _write_bytes(
                    page.screenshot(),
                    name="final_state.png",
                    content_type="image/png",
                    kind="image",
                )
        finally:
            browser.close()

    return {
        "final_url": final_url,
        "title": title,
        "screenshot": screenshot_ref,
        "actions_executed": len(actions),
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_click_fill" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add browser_click_fill node"
```

---

## Task 8: `browser_pdf_from_url`

Navigates to a URL and uses Playwright's Chromium print engine to render a PDF — higher fidelity than weasyprint for live web pages.

**Files:**
- Modify: `packages/nodes/noodle_nodes/browser_automation.py`
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_browser_automation.py

FAKE_PDF = b"%PDF-1.4\n" + b"\x00" * 64


def test_browser_pdf_from_url_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_pdf_from_url
    with pytest.raises(ValueError, match="url is required"):
        browser_pdf_from_url(input=None, url="")


def test_browser_pdf_from_url_returns_pdf_artifact(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        result = browser_pdf_from_url(url="https://example.com")

    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "application/pdf"
    assert result["url"] == "https://example.com"
    assert result["size_bytes"] == len(FAKE_PDF)


def test_browser_pdf_from_url_passes_format_to_page(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        browser_pdf_from_url(url="https://example.com", paper_format="A4", print_background=True)

    call_kwargs = mock_page.pdf.call_args[1]
    assert call_kwargs["format"] == "A4"
    assert call_kwargs["print_background"] is True


def test_browser_pdf_from_url_uses_input_as_url(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        result = browser_pdf_from_url(input="https://from-input.com")

    assert result["url"] == "https://from-input.com"
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_pdf_from_url" -v
```

Expected: 4 failures

- [ ] **Step 3: Implement `browser_pdf_from_url`**

```python
@node(
    name="Browser PDF from URL",
    id="browser_pdf_from_url",
    category="Browser & Web",
    icon="page",
    tool_side_effecting=True,
    requirements=["playwright>=1.40"],
    params={
        "url": {"placeholder": "https://example.com/report"},
        "paper_format": {
            "choices": ["Letter", "A4", "Legal", "Tabloid", "A3"],
            "description": "Paper size for the PDF output.",
        },
        "print_background": {"description": "Include CSS backgrounds in the PDF"},
        "wait_for": {
            "choices": ["networkidle", "load", "domcontentloaded"],
            "description": "Wait condition before printing. networkidle is most reliable.",
        },
        "page_timeout_ms": {"description": "Maximum page load wait in ms (default 30000)"},
        "filename": {"placeholder": "page.pdf"},
    },
)
def browser_pdf_from_url(
    input=None,
    url: str = "",
    paper_format: str = "Letter",
    print_background: bool = True,
    wait_for: str = "networkidle",
    page_timeout_ms: int = 30_000,
    filename: str = "page.pdf",
) -> dict:
    """Print a web page to a PDF artifact using the Chromium browser print engine.

    Requires: playwright install chromium  (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError("url is required — set a URL in the node config or wire one as input.")

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required. Add playwright to the workflow environment, "
            "rebuild it, then run 'playwright install chromium' inside the environment."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            try:
                page.goto(target_url, wait_until=wait_for, timeout=page_timeout_ms)
            except (PWTimeout, TimeoutError) as exc:
                raise RuntimeError(
                    f"Page timed out after {page_timeout_ms}ms loading {target_url}. "
                    "Increase page_timeout_ms or check the URL is reachable."
                ) from exc
            pdf_bytes: bytes = page.pdf(
                format=paper_format,
                print_background=print_background,
            )
        finally:
            browser.close()

    artifact = _write_bytes(
        pdf_bytes,
        name=filename or "page.pdf",
        content_type="application/pdf",
        kind="document",
    )
    return {"artifact": artifact, "url": target_url, "size_bytes": len(pdf_bytes)}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "browser_pdf_from_url" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/browser_automation.py \
        packages/nodes/tests/test_browser_automation.py
git commit -m "feat(nodes): add browser_pdf_from_url node"
```

---

## Task 9: Registration verification + full test suite

**Files:**
- Modify: `packages/nodes/tests/test_browser_automation.py`

- [ ] **Step 1: Write the registration tests**

```python
# Add to test_browser_automation.py

def test_browser_automation_nodes_registered() -> None:
    from noodle.sdk import registry
    ids = {m.id for m in registry.manifests()}
    expected = {
        "html_extract",
        "web_feed_parse",
        "graphql_request",
        "browser_screenshot",
        "browser_scrape",
        "browser_click_fill",
        "browser_pdf_from_url",
    }
    assert expected <= ids, f"Missing from registry: {expected - ids}"


def test_browser_automation_nodes_have_requirements() -> None:
    """Every node must declare requirements (none use only stdlib)."""
    from noodle.sdk import registry
    ids_with_reqs = {
        "html_extract",
        "web_feed_parse",
        "graphql_request",
        "browser_screenshot",
        "browser_scrape",
        "browser_click_fill",
        "browser_pdf_from_url",
    }
    manifests = {m.id: m for m in registry.manifests()}
    for node_id in ids_with_reqs:
        m = manifests.get(node_id)
        assert m is not None, f"Node {node_id} not in registry"
        assert m.requirements, f"Node {node_id} has no requirements declared"


def test_browser_nodes_are_side_effecting() -> None:
    """All browser_* nodes must be marked tool_side_effecting."""
    from noodle.sdk import registry
    browser_ids = {
        "browser_screenshot",
        "browser_scrape",
        "browser_click_fill",
        "browser_pdf_from_url",
    }
    manifests = {m.id: m for m in registry.manifests()}
    for node_id in browser_ids:
        m = manifests.get(node_id)
        assert m is not None, f"{node_id} not in registry"
        assert getattr(m, "tool_side_effecting", True), f"{node_id} must be tool_side_effecting"


def test_import_does_not_import_optional_packages() -> None:
    """Importing browser_automation must not pull in heavy packages at module scope."""
    import importlib
    forbidden = {"playwright", "bs4", "feedparser", "requests"}
    pre = set(sys.modules.keys())
    if "noodle_nodes.browser_automation" in sys.modules:
        importlib.reload(sys.modules["noodle_nodes.browser_automation"])
    imported = set(sys.modules.keys()) - pre
    leaked = forbidden & imported
    assert not leaked, f"Optional packages leaked into module scope: {leaked}"
```

- [ ] **Step 2: Run registration tests**

```bash
cd packages/nodes
uv run pytest tests/test_browser_automation.py -k "registered or requirements or side_effecting or optional_packages" -v
```

Expected: 4 PASSED

- [ ] **Step 3: Run the full test suite — no regressions**

```bash
cd packages/nodes
uv run pytest tests/ -v --tb=short
```

Expected: all existing tests pass; new tests pass or skip (if optional packages not installed)

- [ ] **Step 4: Verify top-level import still works**

```bash
cd packages/nodes
uv run python -c "import noodle_nodes; print('OK')"
```

Expected: `OK` with no errors

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/tests/test_browser_automation.py
git commit -m "test(nodes): add registration and import-safety tests for browser_automation"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| No optional package imported at module scope | Task 1 scaffold + Task 9 import-safety test |
| `requirements=[...]` on every node | Task 9 registration test asserts non-empty |
| Lazy import with `RuntimeError` + install message | Tasks 2, 3, 4, 5 missing-package tests |
| `browser_*` marked `tool_side_effecting=True` | Task 9 side-effecting test |
| `playwright install chromium` surfaced in error | Noted in RuntimeError messages Tasks 5–8 |
| `browser_screenshot` ★ | Task 5 |
| `browser_scrape` ★ | Task 6 |
| `html_extract` | Task 2 |
| `web_feed_parse` | Task 3 |
| `graphql_request` | Task 4 |
| `browser_click_fill` | Task 7 |
| `browser_pdf_from_url` | Task 8 |
| `wait_for` config param on browser nodes | Tasks 5, 6, 8 (param + impl) |
| `page_timeout_ms` on all browser nodes | Tasks 5, 6, 7, 8 (param + impl) |
| Anti-bot / timeout → clear error message | `RuntimeError` with instructions in Tasks 5–8 |
| `browser_scrape` pagination | Task 6 (`pagination_next_selector` + `max_pages` + pagination test) |
| Selector not found → `ValueError` with message | Task 5 `test_browser_screenshot_raises_when_selector_not_found` |
| Missing field in scrape → empty string | Task 6 `test_browser_scrape_missing_field_returns_empty_string` |
| Unknown action in click_fill → `ValueError` | Task 7 `test_browser_click_fill_raises_on_unknown_action` |
| Invalid feed → raise with detail | Task 3 `test_web_feed_parse_raises_on_invalid_feed` |
| GraphQL errors surface in output | Task 4 `test_graphql_request_surfaces_graphql_errors` |
| Large outputs → artifact refs or DatasetRefs | All nodes return `ArtifactRef` or `DatasetRef`, never inline blobs |

**Nodes deferred (not in this Wave):** `browser_auth_session`, `browser_wait_download`, `sitemap_crawl`, `websocket_send_receive` — depend on heavier session/state management or streaming, ship in a later pass.

**Type consistency:** `_make_pw_mocks()` returns `(mock_sync_playwright, mock_page, mock_pw_sync_api)` — used consistently across all 4 browser-node test groups. `records_to_dataset` is imported the same way in Tasks 2, 3, 6 as in the document_intelligence pattern.
