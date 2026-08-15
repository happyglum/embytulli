"""Thin wrapper around the bits of the Emby REST API we need: fetching a
poster image for an item so it can be attached to Discord notifications.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger("embytulli.emby_client")


class EmbyClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def fetch_primary_image(self, item_id: str, max_width: int = 400) -> tuple[bytes, str] | None:
        """Returns (image_bytes, content_type) or None if unavailable."""
        if not item_id:
            return None
        url = f"{self.base_url}/emby/Items/{item_id}/Images/Primary"
        params = {"maxWidth": max_width, "quality": 90}
        headers = {"X-Emby-Token": self.api_key} if self.api_key else {}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code == 200 and resp.content:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return resp.content, content_type
            logger.warning("Emby image fetch for item %s returned status %s", item_id, resp.status_code)
        except requests.RequestException as e:
            logger.warning("Emby image fetch for item %s failed: %s", item_id, e)
        return None
