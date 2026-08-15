"""Builds and sends rich Discord embeds for Emby events."""
from __future__ import annotations

import json
import logging
import mimetypes

import requests

from config import AppConfig
from emby_client import EmbyClient
from event_parser import (
    EmbyEvent,
    LIBRARY_NEW,
    MARK_PLAYED,
    MARK_UNPLAYED,
    PLAYBACK_PAUSE,
    PLAYBACK_START,
    PLAYBACK_STOP,
    PLAYBACK_UNPAUSE,
    TEST_EVENT,
)
from formatting import format_duration, progress_bar, provider_links, quality_string

logger = logging.getLogger("embytulli.discord_notifier")

# (title prefix, emoji, color) per normalized event type
_EVENT_STYLE = {
    PLAYBACK_START: ("Now Playing", "▶️", 0x2ECC71),      # green
    PLAYBACK_PAUSE: ("Paused", "⏸️", 0xF39C12),           # orange
    PLAYBACK_UNPAUSE: ("Resumed", "▶️", 0x3498DB),        # blue
    PLAYBACK_STOP: ("Stopped", "⏹️", 0xE74C3C),           # red
    LIBRARY_NEW: ("Added to Library", "\U0001f195", 0x9B59B6),      # purple
    MARK_PLAYED: ("Marked Played", "✅", 0x95A5A6),             # grey
    MARK_UNPLAYED: ("Marked Unplayed", "↩️", 0x95A5A6),   # grey
    TEST_EVENT: ("Test Notification", "\U0001f6a7", 0x7289DA),      # blurple
}


class DiscordNotifier:
    def __init__(self, config: AppConfig, emby_client: EmbyClient):
        self.config = config
        self.emby_client = emby_client

    def build_embed(self, event: EmbyEvent) -> dict:
        title_prefix, emoji, color = _EVENT_STYLE.get(
            event.event_type, ("Event", "ℹ️", 0x7289DA)
        )

        embed: dict = {
            "title": f"{emoji} {title_prefix}",
            "description": f"**{event.display_title}**",
            "color": color,
            "fields": [],
            "footer": {"text": f"Emby" + (f" • {event.server_name}" if event.server_name else "")},
            "timestamp": None,  # Discord fills relative time from this if set; left out to avoid tz bugs
        }
        embed.pop("timestamp")

        links = provider_links(event) if self.config.notifications.include_provider_links else []
        if links:
            # Makes the embed title itself clickable, preferring TMDb since
            # it's Emby's default metadata source.
            preferred = next((url for label, url in links if label == "TMDb"), links[0][1])
            embed["url"] = preferred

        fields = embed["fields"]
        if event.user_name:
            fields.append({"name": "User", "value": event.user_name, "inline": True})
        player_bits = [b for b in [event.client_name, event.device_name] if b]
        if player_bits:
            fields.append({"name": "Player", "value": " / ".join(player_bits), "inline": True})

        quality = quality_string(event)
        if quality:
            fields.append({"name": "Quality", "value": quality, "inline": True})

        duration = format_duration(event.runtime_ticks)
        if duration and event.event_type in (PLAYBACK_START, LIBRARY_NEW):
            fields.append({"name": "Runtime", "value": duration, "inline": True})

        if event.library_name:
            fields.append({"name": "Library", "value": event.library_name, "inline": True})

        if (
            self.config.notifications.include_progress_bar
            and event.event_type in (PLAYBACK_STOP, PLAYBACK_PAUSE)
        ):
            bar = progress_bar(event.progress_fraction)
            if bar:
                fields.append({"name": "Progress", "value": bar, "inline": False})

        if self.config.notifications.include_summary and event.overview:
            summary = event.overview.strip()
            if len(summary) > 300:
                summary = summary[:297] + "..."
            fields.append({"name": "Summary", "value": summary, "inline": False})

        if links:
            value = " • ".join(f"[{label}]({url})" for label, url in links)
            fields.append({"name": "Links", "value": value, "inline": False})

        return embed

    def send(self, event: EmbyEvent) -> bool:
        embed = self.build_embed(event)

        payload = {
            "username": self.config.discord.username or None,
            "avatar_url": self.config.discord.avatar_url or None,
            "embeds": [embed],
        }
        payload = {k: v for k, v in payload.items() if v}
        payload["embeds"] = [embed]  # always present even if filtered above

        files = None
        image_bytes = None
        if self.config.notifications.include_poster and event.item_id:
            fetched = self.emby_client.fetch_primary_image(event.item_id)
            if fetched:
                image_bytes, content_type = fetched
                ext = mimetypes.guess_extension(content_type) or ".jpg"
                filename = f"poster{ext}"
                embed["thumbnail"] = {"url": f"attachment://{filename}"}
                files = {"file": (filename, image_bytes, content_type)}

        try:
            if files:
                resp = requests.post(
                    self.config.discord.webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                    timeout=15,
                )
            else:
                resp = requests.post(self.config.discord.webhook_url, json=payload, timeout=15)

            if resp.status_code >= 300:
                logger.error("Discord webhook returned %s: %s", resp.status_code, resp.text[:500])
                return False
            return True
        except requests.RequestException as e:
            logger.error("Failed to send Discord notification: %s", e)
            return False
