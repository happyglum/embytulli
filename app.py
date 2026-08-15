"""embytulli - a lightweight Tautulli-style Discord notifier for Emby.

Receives webhook POSTs from Emby's built-in Webhooks feature, normalizes
the payload, and sends a rich Discord embed (poster, user, player, quality,
progress bar). Every event is also logged to a local SQLite database as a
foundation for a future analytics/dashboard phase.

Run for development:
    python app.py

Run in production (see README.md for the systemd unit):
    gunicorn app:app -b 0.0.0.0:8087
"""
from __future__ import annotations

import logging
import sys

from flask import Flask, jsonify, request

from config import ConfigError, load_config
from db import EventDB
from discord_notifier import DiscordNotifier
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
    parse_webhook_payload,
)

try:
    cfg = load_config()
except ConfigError as e:
    print(f"Config error: {e}", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=getattr(logging, cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("embytulli")

app = Flask(__name__)
emby_client = EmbyClient(cfg.emby.base_url, cfg.emby.api_key)
notifier = DiscordNotifier(cfg, emby_client)
db = EventDB(cfg.database.path) if cfg.database.enabled else None

_EVENT_ENABLED = {
    PLAYBACK_START: lambda n: n.playback_start,
    PLAYBACK_PAUSE: lambda n: n.playback_pause,
    PLAYBACK_UNPAUSE: lambda n: n.playback_unpause,
    PLAYBACK_STOP: lambda n: n.playback_stop,
    LIBRARY_NEW: lambda n: n.library_new,
    MARK_PLAYED: lambda n: n.mark_played,
    MARK_UNPLAYED: lambda n: n.mark_unplayed,
    TEST_EVENT: lambda n: True,
}


def _should_notify(event: EmbyEvent) -> bool:
    check = _EVENT_ENABLED.get(event.event_type)
    if check is None:
        return False
    if not check(cfg.notifications):
        return False
    if event.user_name and event.user_name.lower() in cfg.notifications.ignore_users:
        return False
    if event.library_name and event.library_name.lower() in cfg.notifications.ignore_libraries:
        return False
    return True


def _check_secret() -> bool:
    if not cfg.server.shared_secret:
        return True
    return request.args.get("secret") == cfg.server.shared_secret


@app.route(cfg.server.webhook_path, methods=["POST"])
def emby_webhook():
    if not _check_secret():
        logger.warning("Rejected webhook request with missing/invalid secret from %s", request.remote_addr)
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        logger.warning("Received webhook with non-JSON or empty body from %s", request.remote_addr)
        return jsonify({"error": "expected JSON body"}), 400

    try:
        event = parse_webhook_payload(payload)
    except Exception:
        logger.exception("Failed to parse webhook payload")
        return jsonify({"error": "failed to parse payload"}), 400

    logger.info(
        "Received event=%s (raw=%s) item=%r user=%r",
        event.event_type, event.raw_event, event.item_name, event.user_name,
    )

    notified = False
    if _should_notify(event):
        if (
            event.event_type == LIBRARY_NEW
            and db is not None
            and event.item_id
            and db.was_recently_notified(
                event.item_id, LIBRARY_NEW, cfg.notifications.library_new_dedupe_minutes
            )
        ):
            logger.info(
                "Skipping duplicate library.new notification for item_id=%s item=%r "
                "(already notified within the last %s minutes)",
                event.item_id, event.item_name, cfg.notifications.library_new_dedupe_minutes,
            )
        else:
            notified = notifier.send(event)
            if not notified:
                logger.error("Notification send failed for event=%s item=%r", event.event_type, event.item_name)
    else:
        logger.debug("Skipping notification for event=%s (disabled or filtered)", event.event_type)

    if db is not None:
        db.log_event(event, notified)

    return jsonify({"status": "ok", "notified": notified}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/test", methods=["GET", "POST"])
def send_test_notification():
    """Fires a synthetic playback.start event through the full pipeline so
    you can verify Discord formatting without waiting for real playback."""
    sample_payload = {
        "Event": "playback.start",
        "Server": {"Name": cfg.emby.base_url, "Id": "test-server"},
        "User": {"Name": "TestUser"},
        "Session": {"DeviceName": "Living Room TV", "Client": "Emby Web"},
        "Item": {
            "Name": "Test Episode Title",
            "Type": "Episode",
            "SeriesName": "Test Show",
            "ParentIndexNumber": 1,
            "IndexNumber": 5,
            "ProductionYear": 2024,
            "Overview": "This is a test notification sent by embytulli's /test endpoint "
                        "to verify your Discord webhook and formatting are working.",
            "RunTimeTicks": 24 * 60 * 10_000_000,
            "Id": "0",
            "MediaStreams": [
                {"Type": "Video", "Codec": "hevc", "Width": 3840, "Height": 2160, "VideoRange": "HDR10"},
                {"Type": "Audio", "Codec": "eac3", "Channels": 6, "DisplayLanguage": "English"},
            ],
        },
    }
    event = parse_webhook_payload(sample_payload)
    ok = notifier.send(event)
    return jsonify({"status": "ok" if ok else "failed", "sent": ok}), (200 if ok else 502)


if __name__ == "__main__":
    logger.info("Starting embytulli on %s:%s%s", cfg.server.host, cfg.server.port, cfg.server.webhook_path)
    app.run(host=cfg.server.host, port=cfg.server.port)
