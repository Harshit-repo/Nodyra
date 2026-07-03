"""Dynamic option loaders for Slack v2 nodes."""

from __future__ import annotations

from typing import Any

from nodyra_nodes.integrations_v2.dynamic_options import DynamicOption, register_loader
from nodyra_nodes.integrations_v2.providers.slack.operations import list_channels


def _list_channels(credentials: Any = None, **_kwargs: Any) -> list[DynamicOption]:
    """Return the workspace's channels as selectable options (value = channel id).

    Reuses the list-channels executor so the editor dropdown shows the user's
    real channels; the chosen channel id is what message operations send to.
    """
    result = list_channels(credentials=credentials, max_results=200)
    channels = result.get("channels", []) if isinstance(result, dict) else []
    options: list[DynamicOption] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_id = channel.get("id")
        if not channel_id:
            continue
        name = channel.get("name")
        options.append(
            DynamicOption(
                value=str(channel_id),
                label=f"#{name}" if name else str(channel_id),
            )
        )
    return options


register_loader("slack.list_channels", _list_channels)
