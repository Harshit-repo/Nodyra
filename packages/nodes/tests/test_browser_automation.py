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

# Snapshot immediately after module import — captures only what leaked at module scope,
# not what later tests lazily import from installed packages.
_MODULES_AT_IMPORT = frozenset(sys.modules)


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
    assert hasattr(mod, "html_extract_records")
    assert hasattr(mod, "web_feed_parse")
    assert hasattr(mod, "browser_screenshot")
    assert hasattr(mod, "browser_scrape")
    assert hasattr(mod, "browser_click_fill")
    assert hasattr(mod, "browser_pdf_from_url")


# ---------------------------------------------------------------------------
# html_extract
# ---------------------------------------------------------------------------

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
    from noodle_nodes.browser_automation import html_extract_records
    with pytest.raises(ValueError, match="input is required"):
        html_extract_records(input=None)


def test_html_extract_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "bs4":
            raise ImportError("No module named 'bs4'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import html_extract_records
    with pytest.raises(RuntimeError, match="beautifulsoup4"):
        html_extract_records(input=HTML_SAMPLE)


def test_html_extract_container_returns_records(store_ctx) -> None:
    pytest.importorskip("bs4")
    from noodle_nodes.browser_automation import html_extract_records

    result = html_extract_records(
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
    from noodle_nodes.browser_automation import html_extract_records

    result = html_extract_records(
        input=HTML_SAMPLE,
        selectors_json='{"description": "#desc"}',
    )
    assert result["records_found"] == 1
    assert result["records"][0]["description"] == "Best widgets ever."


def test_html_extract_missing_selector_returns_empty_string(store_ctx) -> None:
    pytest.importorskip("bs4")
    from noodle_nodes.browser_automation import html_extract_records

    result = html_extract_records(
        input=HTML_SAMPLE,
        container_selector="li.product",
        selectors_json='{"name": "h2", "missing": ".no-such-class"}',
    )
    assert result["records"][0]["missing"] == ""


# ---------------------------------------------------------------------------
# web_feed_parse
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# sitemap_crawl — XXE / entity expansion safety (F-SEC-1)
# ---------------------------------------------------------------------------

_VALID_SITEMAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/page2</loc></url>
</urlset>
"""

_BILLION_LAUGHS = """\
<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&lol5;</loc></url>
</urlset>
"""

_EXTERNAL_ENTITY = """\
<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&xxe;</loc></url>
</urlset>
"""


def test_sitemap_crawl_rejects_entity_expansion_bomb(store_ctx) -> None:
    """sitemap_crawl must raise ValueError on a billion-laughs XML bomb.

    Before the fix: stdlib ElementTree.fromstring() expands entities, causing
    exponential memory growth. After the fix: defusedxml raises ParseError
    which is caught and re-raised as ValueError.

    Pass the payload directly as `input` — the internal _load closure treats
    any string that doesn't start with http(s):// as raw XML.
    """
    from noodle_nodes.browser_automation import sitemap_crawl

    with pytest.raises(ValueError, match="(?i)(invalid|sitemap|xml|entity|dtd)"):
        sitemap_crawl(input=_BILLION_LAUGHS)


def test_sitemap_crawl_rejects_external_entity(store_ctx) -> None:
    """sitemap_crawl must raise ValueError on an external-entity XXE payload.

    Before the fix: stdlib ElementTree.fromstring() may follow SYSTEM URIs
    and inject file contents into parsed fields. After the fix: defusedxml
    blocks external entity references at parse time.
    """
    from noodle_nodes.browser_automation import sitemap_crawl

    with pytest.raises(ValueError, match="(?i)(invalid|sitemap|xml|entity|dtd)"):
        sitemap_crawl(input=_EXTERNAL_ENTITY)


def test_sitemap_crawl_valid_xml_still_works(store_ctx) -> None:
    """Valid sitemap XML must still be parsed correctly after the fix."""
    pytest.importorskip("defusedxml")
    from noodle_nodes.browser_automation import sitemap_crawl

    result = sitemap_crawl(input=_VALID_SITEMAP)
    assert result["url_count"] == 2


# ---------------------------------------------------------------------------
# browser_screenshot
# ---------------------------------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_browser_screenshot_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_screenshot
    with pytest.raises(ValueError, match="url is required"):
        browser_screenshot(input=None, url="")


def test_browser_screenshot_raises_missing_package(store_ctx, monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name in ("playwright", "playwright.sync_api"):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from noodle_nodes.browser_automation import browser_screenshot
    with pytest.raises(RuntimeError, match="playwright"):
        browser_screenshot(url="https://example.com")


def test_browser_screenshot_returns_png_artifact(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.screenshot.return_value = FAKE_PNG

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(url="https://example.com")

    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "image/png"
    assert result["url"] == "https://example.com"
    assert result["size_bytes"] == len(FAKE_PNG)


def test_browser_screenshot_uses_url_from_input(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.screenshot.return_value = FAKE_PNG

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(input="https://from-input.com")

    assert result["url"] == "https://from-input.com"


def test_browser_screenshot_selector_mode(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_element = MagicMock()
    mock_element.screenshot.return_value = FAKE_PNG
    mock_page.query_selector.return_value = mock_element

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_screenshot
        result = browser_screenshot(url="https://example.com", selector="#hero")

    mock_page.query_selector.assert_called_with("#hero")
    mock_element.screenshot.assert_called_once()
    assert is_artifact_ref(result["artifact"])


def test_browser_screenshot_raises_when_selector_not_found(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.query_selector.return_value = None

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_screenshot
        with pytest.raises(ValueError, match="matched no elements"):
            browser_screenshot(url="https://example.com", selector=".no-such-element")


def test_browser_screenshot_timeout_raises_runtime_error(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.goto.side_effect = TimeoutError("Timeout")

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_screenshot
        with pytest.raises(RuntimeError, match="timed out"):
            browser_screenshot(url="https://example.com", page_timeout_ms=5000)


# ---------------------------------------------------------------------------
# browser_scrape
# ---------------------------------------------------------------------------

def test_browser_scrape_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_scrape
    with pytest.raises(ValueError, match="url is required"):
        browser_scrape(input=None, url="")


def test_browser_scrape_raises_without_selectors(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    with patch_dict_pw(mock_pw_sync_api):
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
    mock_page.query_selector.return_value = None  # no pagination link

    with patch_dict_pw(mock_pw_sync_api):
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
    mock_el.query_selector.return_value = None
    mock_page.query_selector_all.return_value = [mock_el]
    mock_page.query_selector.return_value = None

    with patch_dict_pw(mock_pw_sync_api):
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

    def qsa_side_effect(sel):
        if sel == "li.item":
            call_count["n"] += 1
            mock_el = MagicMock()
            mock_el.query_selector.return_value = MagicMock(
                text_content=MagicMock(return_value=f"Item page {call_count['n']}")
            )
            return [mock_el]
        return []

    mock_page.query_selector_all.side_effect = qsa_side_effect
    next_link_mock = MagicMock()
    mock_page.query_selector.side_effect = [next_link_mock, None]

    with patch_dict_pw(mock_pw_sync_api):
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


# ---------------------------------------------------------------------------
# browser_click_fill
# ---------------------------------------------------------------------------

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

    with patch_dict_pw(mock_pw_sync_api):
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

    actions = [{"action": "wait_for_selector", "selector": ".success-banner", "timeout_ms": 3000}]

    with patch_dict_pw(mock_pw_sync_api):
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

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_click_fill
        with pytest.raises(ValueError, match="Unknown action"):
            browser_click_fill(url="https://example.com", actions_json=json.dumps(actions))


# ---------------------------------------------------------------------------
# browser_pdf_from_url
# ---------------------------------------------------------------------------

FAKE_PDF = b"%PDF-1.4\n" + b"\x00" * 64


def test_browser_pdf_from_url_raises_without_url(store_ctx) -> None:
    from noodle_nodes.browser_automation import browser_pdf_from_url
    with pytest.raises(ValueError, match="url is required"):
        browser_pdf_from_url(input=None, url="")


def test_browser_pdf_from_url_returns_pdf_artifact(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        result = browser_pdf_from_url(url="https://example.com")

    assert is_artifact_ref(result["artifact"])
    assert result["artifact"]["content_type"] == "application/pdf"
    assert result["url"] == "https://example.com"
    assert result["size_bytes"] == len(FAKE_PDF)


def test_browser_pdf_from_url_passes_format_to_page(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        browser_pdf_from_url(url="https://example.com", paper_format="A4", print_background=True)

    call_kwargs = mock_page.pdf.call_args[1]
    assert call_kwargs["format"] == "A4"
    assert call_kwargs["print_background"] is True


def test_browser_pdf_from_url_uses_input_as_url(store_ctx) -> None:
    mock_sync_playwright, mock_page, mock_pw_sync_api = _make_pw_mocks()
    mock_page.pdf.return_value = FAKE_PDF

    with patch_dict_pw(mock_pw_sync_api):
        from noodle_nodes.browser_automation import browser_pdf_from_url
        result = browser_pdf_from_url(input="https://from-input.com")

    assert result["url"] == "https://from-input.com"


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_browser_automation_nodes_registered() -> None:
    from noodle.sdk import registry
    ids = {m.id for m in registry.manifests()}
    expected = {
        "html_extract_records",
        "web_feed_parse",
        "browser_screenshot",
        "browser_scrape",
        "browser_click_fill",
        "browser_pdf_from_url",
    }
    assert expected <= ids, f"Missing from registry: {expected - ids}"


def test_browser_automation_nodes_have_requirements() -> None:
    from noodle.sdk import registry
    ids_with_reqs = {
        "html_extract_records",
        "web_feed_parse",
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
    forbidden = {"playwright", "bs4", "feedparser"}
    leaked = forbidden & _MODULES_AT_IMPORT
    assert not leaked, f"Optional packages leaked into module scope: {leaked}"


# ---------------------------------------------------------------------------
# Shared helper — placed after tests so it can reference FAKE_PNG etc.
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def patch_dict_pw(mock_pw_sync_api):
    """Context manager: patch sys.modules so lazy playwright imports resolve to mocks."""
    with patch.dict(sys.modules, {
        "playwright": MagicMock(),
        "playwright.sync_api": mock_pw_sync_api,
    }):
        yield
