"""Browser and web automation nodes — Playwright, HTML extraction, RSS, GraphQL.

All heavy dependencies are lazy-imported inside each function body.
No optional package is imported at module scope.
"""

from __future__ import annotations

import json
from typing import Any
from xml.etree import ElementTree

from defusedxml import DefusedXmlException as _DefusedXmlException
from defusedxml.ElementTree import ParseError as _XmlParseError
from defusedxml.ElementTree import fromstring as _safe_xml_fromstring

from noodle.artifacts import is_artifact_ref as _is_artifact_ref
from noodle.artifacts import read_bytes as _read_bytes
from noodle.artifacts import write_bytes as _write_bytes
from noodle.sdk import node

# ---------------------------------------------------------------------------
# html_extract
# ---------------------------------------------------------------------------

@node(
    name="HTML Extract Records",
    id="html_extract_records",
    category="Browser & Web",
    icon="code",
    requirements=["beautifulsoup4>=4.12"],
    params={
        "container_selector": {
            "placeholder": "li.product  (blank = whole page, one record)",
            "description": (
                "CSS selector for the repeating parent element. Each match "
                "becomes one record."
            ),
        },
        "selectors_json": {
            "multiline": True,
            "placeholder": '{"title": "h2", "price": ".price", "link": "a"}',
            "description": (
                "JSON mapping of field name → CSS selector (relative to "
                "container, or page if no container)."
            ),
        },
    },
)
def html_extract_records(
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
    elif _is_artifact_ref(input):
        html = _read_bytes(input).decode("utf-8", errors="replace")
    else:
        html = str(input)

    try:
        field_selectors: dict[str, str] = (
            json.loads(selectors_json) if selectors_json.strip() else {}
        )
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


# ---------------------------------------------------------------------------
# sitemap_crawl
# ---------------------------------------------------------------------------

@node(
    name="Sitemap Crawl",
    id="sitemap_crawl",
    category="Browser & Web",
    icon="globe",
    requirements=["requests>=2.28"],
    params={
        "url": {"placeholder": "https://example.com/sitemap.xml"},
        "max_urls": {"description": "Maximum URL entries to return."},
        "include_nested_sitemaps": {
            "description": "Fetch sitemapindex children when input is a sitemap index.",
        },
        "timeout_seconds": {"description": "HTTP timeout per sitemap request."},
    },
)
def sitemap_crawl(
    input=None,
    url: str = "",
    max_urls: int = 1000,
    include_nested_sitemaps: bool = True,
    timeout_seconds: float = 15.0,
) -> dict:
    """Parse a sitemap URL/XML and return discovered URLs as a DatasetRef."""
    try:
        import requests  # type: ignore[import-not-found]  # noqa: F401 - ensures dep present
    except ImportError as exc:
        raise RuntimeError("Sitemap Crawl requires requests>=2.28.") from exc

    from noodle_nodes.datasets import records_to_dataset
    from noodle_nodes.http_security import safe_request

    source = url or (str(input) if input is not None and not _is_artifact_ref(input) else "")
    if _is_artifact_ref(input):
        source = _read_bytes(input).decode("utf-8", errors="replace")
    if not source:
        raise ValueError("url or XML input is required.")

    def _load(candidate: str) -> tuple[str, str]:
        if candidate.startswith(("http://", "https://")):
            # SEC-2: candidate is a user-supplied URL — route through the SSRF
            # guard so a sitemap fetch cannot reach internal/metadata endpoints.
            resp = safe_request(
                "GET", candidate, timeout=float(timeout_seconds or 15),
                context="sitemap_crawl",
            )
            resp.raise_for_status()
            return candidate, resp.text
        return "input", candidate

    records: list[dict[str, Any]] = []
    queue = [source]
    seen_sitemaps: set[str] = set()
    queued_sitemaps: set[str] = set()
    max_count = max(1, int(max_urls or 1000))

    while queue and len(records) < max_count and len(seen_sitemaps) < 25:
        sitemap_ref = queue.pop(0)
        location = sitemap_ref if sitemap_ref.startswith(("http://", "https://")) else "input"
        if location in seen_sitemaps:
            continue
        seen_sitemaps.add(location)
        location, xml_text = _load(sitemap_ref)

        try:
            root = _safe_xml_fromstring(xml_text.encode("utf-8"))
        except (_XmlParseError, _DefusedXmlException) as exc:
            raise ValueError(f"Invalid sitemap XML from {location}: {exc}") from exc

        def _child_text(element: ElementTree.Element, local_name: str) -> str:
            for child in element:
                if child.tag.rsplit("}", 1)[-1] == local_name:
                    return (child.text or "").strip()
            return ""

        root_name = root.tag.rsplit("}", 1)[-1]
        if root_name == "sitemapindex":
            for sitemap in root:
                loc = _child_text(sitemap, "loc")
                if not loc:
                    continue
                records.append(
                    {
                        "loc": loc,
                        "type": "sitemap",
                        "lastmod": _child_text(sitemap, "lastmod"),
                        "source_sitemap": location,
                    }
                )
                if (
                    include_nested_sitemaps
                    and loc not in seen_sitemaps
                    and loc not in queued_sitemaps
                ):
                    queued_sitemaps.add(loc)
                    queue.append(loc)
                if len(records) >= max_count:
                    break
            continue

        for url_el in root:
            loc = _child_text(url_el, "loc")
            if not loc:
                continue
            records.append(
                {
                    "loc": loc,
                    "type": "url",
                    "lastmod": _child_text(url_el, "lastmod"),
                    "changefreq": _child_text(url_el, "changefreq"),
                    "priority": _child_text(url_el, "priority"),
                    "source_sitemap": location,
                }
            )
            if len(records) >= max_count:
                break

    dataset = records_to_dataset(records, name="sitemap-urls.parquet") if records else None
    return {
        "records": records,
        "url_count": sum(1 for row in records if row.get("type") == "url"),
        "sitemap_count": len(seen_sitemaps),
        "dataset": dataset,
    }


# ---------------------------------------------------------------------------
# web_feed_parse
# ---------------------------------------------------------------------------

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



# ---------------------------------------------------------------------------
# browser_screenshot  ★
# ---------------------------------------------------------------------------

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
            "description": (
                "CSS selector to screenshot a specific element. Leave blank "
                "to capture the full page."
            ),
        },
        "full_page": {
            "description": (
                "Capture the full scrollable page (ignored when selector is set)"
            )
        },
        "viewport_width": {"description": "Browser viewport width in pixels (default 1280)"},
        "viewport_height": {"description": "Browser viewport height in pixels (default 800)"},
        "wait_for": {
            "choices": ["networkidle", "load", "domcontentloaded"],
            "description": (
                "Wait condition before capturing. networkidle waits for no "
                "network activity."
            ),
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

    Requires: playwright install chromium
    (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError(
            "url is required — set a URL in the node config or wire one as input."
        )

    try:
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
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


# ---------------------------------------------------------------------------
# browser_scrape  ★
# ---------------------------------------------------------------------------

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
            "description": (
                "CSS selector for the repeating element. Each match becomes "
                "one record."
            ),
        },
        "selectors_json": {
            "multiline": True,
            "placeholder": '{"title": "h2.name", "price": ".price", "link": "a[href]"}',
            "description": (
                "JSON mapping of field name → CSS selector (relative to each "
                "container)."
            ),
        },
        "wait_for": {
            "choices": ["networkidle", "load", "domcontentloaded"],
            "description": "Wait condition before extracting. networkidle is most reliable.",
        },
        "pagination_next_selector": {
            "placeholder": "a.next-page",
            "description": (
                "CSS selector for the 'next page' link. Leave blank to disable "
                "pagination."
            ),
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

    Requires: playwright install chromium
    (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError(
            "url is required — set a URL in the node config or wire one as input."
        )
    if not selectors_json.strip():
        raise ValueError(
            "selectors_json is required — provide a JSON mapping of field name → CSS selector."
        )

    try:
        field_selectors: dict[str, str] = json.loads(selectors_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"selectors_json must be valid JSON: {exc}") from exc

    try:
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
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


# ---------------------------------------------------------------------------
# browser_click_fill
# ---------------------------------------------------------------------------

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
                {
                    "action": "wait_for_selector",
                    "selector": ".dashboard",
                    "timeout_ms": 5000,
                },
            ], indent=2),
            "description": (
                "JSON list of browser actions. "
                "Each: {action: click|fill|select|wait_for_selector|wait, "
                "selector?, value?, timeout_ms?, ms?}."
            ),
        },
        "screenshot_after": {"description": "Capture a screenshot of the final page state"},
        "page_timeout_ms": {
            "description": (
                "Maximum page load wait per navigation in ms (default 30000)"
            )
        },
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

    Requires: playwright install chromium
    (run once in the environment after installing playwright).

    Supported actions:
      {"action": "click", "selector": "CSS"}
      {"action": "fill", "selector": "CSS", "value": "text"}
      {"action": "select", "selector": "CSS", "value": "option-value"}
      {"action": "wait_for_selector", "selector": "CSS", "timeout_ms": 5000}
      {"action": "wait", "ms": 1000}
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError(
            "url is required — set a starting URL in the node config or wire "
            "one as input."
        )
    if not actions_json.strip():
        raise ValueError(
            "actions_json is required — provide a JSON list of browser actions "
            "in the node config."
        )

    try:
        actions: list[dict[str, Any]] = json.loads(actions_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"actions_json must be valid JSON: {exc}") from exc

    try:
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
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


# ---------------------------------------------------------------------------
# browser_pdf_from_url
# ---------------------------------------------------------------------------

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

    Requires: playwright install chromium
    (run once in the environment after installing playwright).
    """
    target_url = url or (str(input) if input is not None else "")
    if not target_url:
        raise ValueError(
            "url is required — set a URL in the node config or wire one as input."
        )

    try:
        from playwright.sync_api import TimeoutError as PWTimeout  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
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
