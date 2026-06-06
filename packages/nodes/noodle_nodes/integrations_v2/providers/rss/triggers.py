"""RSS and Atom feed polling trigger — detects new feed items."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import requests as requests  # named import so tests can monkeypatch

from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerPollContext,
    ProviderTriggerPollResult,
    ProviderTriggerSpec,
)

_MAX_SEEN_GUIDS = 500
_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_rss(root: ET.Element) -> list[dict[str, Any]]:
    items = []
    for item in root.findall(".//item"):
        guid = (item.findtext("guid") or item.findtext("link") or "").strip()
        items.append(
            {
                "guid": guid,
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "description": (item.findtext("description") or "").strip(),
                "pub_date": (item.findtext("pubDate") or "").strip(),
            }
        )
    return items


def _parse_atom(root: ET.Element) -> list[dict[str, Any]]:
    items = []
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        guid = (entry.findtext(f"{{{_ATOM_NS}}}id") or "").strip()
        link_el = entry.find(f"{{{_ATOM_NS}}}link")
        link = (link_el.get("href") or "") if link_el is not None else ""
        items.append(
            {
                "guid": guid,
                "title": (entry.findtext(f"{{{_ATOM_NS}}}title") or "").strip(),
                "link": link.strip(),
                "description": (
                    entry.findtext(f"{{{_ATOM_NS}}}summary") or ""
                ).strip(),
                "pub_date": (
                    entry.findtext(f"{{{_ATOM_NS}}}updated") or ""
                ).strip(),
            }
        )
    return items


def _fetch_items(feed_url: str) -> list[dict[str, Any]]:
    resp = requests.get(
        feed_url,
        timeout=15,
        headers={"User-Agent": "Noodle/1.0 feed-reader"},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    tag = root.tag.lower()
    if "rss" in tag or root.find("channel") is not None:
        return _parse_rss(root)
    if "feed" in tag or f"{{{_ATOM_NS}}}" in root.tag:
        return _parse_atom(root)
    rss = _parse_rss(root)
    return rss if rss else _parse_atom(root)


def poll_rss_feed(ctx: ProviderTriggerPollContext) -> ProviderTriggerPollResult:
    params = ctx.params
    feed_url = str(params.get("feed_url") or "").strip()
    if not feed_url:
        raise ValueError("rss_feed_trigger: feed_url is required")
    try:
        max_items = int(params.get("max_items") or 10)
    except (ValueError, TypeError):
        max_items = 10

    cursor = ctx.cursor
    seen_guids: list[str] | None = cursor.get("seen_guids")

    items = _fetch_items(feed_url)

    if seen_guids is None:
        guids = [it["guid"] for it in items if it["guid"]][-_MAX_SEEN_GUIDS:]
        return ProviderTriggerPollResult(events=[], cursor={"seen_guids": guids})

    seen_set = set(seen_guids)
    new_items = [it for it in items if it["guid"] and it["guid"] not in seen_set]
    new_items = new_items[:max_items]

    events = [
        {
            "provider": "rss",
            "feed_url": feed_url,
            "guid": it["guid"],
            "title": it["title"],
            "link": it["link"],
            "description": it["description"],
            "pub_date": it["pub_date"],
        }
        for it in new_items
    ]

    updated_guids = (
        seen_guids + [it["guid"] for it in new_items]
    )[-_MAX_SEEN_GUIDS:]
    return ProviderTriggerPollResult(events=events, cursor={"seen_guids": updated_guids})


RSS_FEED_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="rss_feed_trigger",
    name="RSS / Atom Feed",
    provider="rss",
    resource="feed",
    event="new_item",
    description="Start a workflow when a new item appears in an RSS or Atom feed.",
    icon="rss",
    params=(
        OperationParamSpec(
            name="feed_url",
            required=True,
            placeholder="https://example.com/feed.xml",
            description="URL of the RSS or Atom feed to watch.",
        ),
        OperationParamSpec(
            name="max_items",
            type="number",
            default=10,
            description="Maximum new items to fire per poll.",
            advanced=True,
        ),
    ),
    requirements=("requests",),
    poll=poll_rss_feed,
    poll_interval_seconds=900,
)


register_provider_trigger(RSS_FEED_TRIGGER_SPEC)
