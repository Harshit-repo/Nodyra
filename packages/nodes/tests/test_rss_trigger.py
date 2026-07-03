"""Tests for the RSS/Atom feed polling trigger."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import nodyra_nodes  # noqa: F401
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.rss import triggers as rss_triggers
from nodyra_nodes.integrations_v2.specs import ProviderTriggerPollContext

RSS_FEED = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Item 1</title>
      <link>https://example.com/1</link>
      <guid>guid-1</guid>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Item 2</title>
      <link>https://example.com/2</link>
      <guid>guid-2</guid>
      <pubDate>Mon, 01 Jan 2024 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <id>atom-1</id>
    <title>Atom Item 1</title>
    <link href="https://example.com/atom/1"/>
    <updated>2024-01-01T12:00:00Z</updated>
  </entry>
</feed>
"""

UNSAFE_FEED = """\
<?xml version="1.0"?>
<!DOCTYPE feed [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss version="2.0">
  <channel>
    <item>
      <title>&xxe;</title>
      <guid>guid-1</guid>
    </item>
  </channel>
</rss>
"""


def _mock_response(text: str, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_rss_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "rss_feed_trigger" in manifests
    m = manifests["rss_feed_trigger"]
    assert m.name == "RSS / Atom Feed"
    assert m.category == "Triggers"


def test_rss_first_run_no_events() -> None:
    """First run with no cursor (seen_guids=None) fires no events."""
    with patch(
        "nodyra_nodes.integrations_v2.providers.rss.triggers.safe_request"
    ) as mock_req:
        mock_req.return_value = _mock_response(RSS_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/feed.xml", "max_items": 10},
                cursor={},
            )
        )
    assert result.events == []
    assert "guid-1" in result.cursor["seen_guids"]
    assert "guid-2" in result.cursor["seen_guids"]


def test_rss_new_items_after_cursor() -> None:
    with patch(
        "nodyra_nodes.integrations_v2.providers.rss.triggers.safe_request"
    ) as mock_req:
        mock_req.return_value = _mock_response(RSS_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/feed.xml", "max_items": 10},
                cursor={"seen_guids": ["guid-1"]},
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["guid"] == "guid-2"
    assert result.events[0]["title"] == "Item 2"
    assert "guid-2" in result.cursor["seen_guids"]


def test_atom_feed_parses_entries() -> None:
    with patch(
        "nodyra_nodes.integrations_v2.providers.rss.triggers.safe_request"
    ) as mock_req:
        mock_req.return_value = _mock_response(ATOM_FEED)
        result = rss_triggers.poll_rss_feed(
            ProviderTriggerPollContext(
                params={"feed_url": "https://example.com/atom.xml", "max_items": 10},
                cursor={"seen_guids": []},
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["guid"] == "atom-1"


def test_rss_rejects_unsafe_xml_entities() -> None:
    with patch(
        "nodyra_nodes.integrations_v2.providers.rss.triggers.safe_request"
    ) as mock_req:
        mock_req.return_value = _mock_response(UNSAFE_FEED)
        with pytest.raises(ValueError, match="unsafe feed XML"):
            rss_triggers.poll_rss_feed(
                ProviderTriggerPollContext(
                    params={"feed_url": "https://example.com/feed.xml", "max_items": 10},
                    cursor={"seen_guids": []},
                )
            )
