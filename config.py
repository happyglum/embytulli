"""Loads and validates config.yaml, with environment-variable overrides.

Env var overrides (useful for Docker/systemd without editing the YAML):
  EMBYTULLI_EMBY_BASE_URL
  EMBYTULLI_EMBY_API_KEY
  EMBYTULLI_DISCORD_WEBHOOK_URL
  EMBYTULLI_SERVER_PORT
  EMBYTULLI_SHARED_SECRET
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class EmbyConfig:
    base_url: str
    api_key: str


@dataclass
class DiscordConfig:
    webhook_url: str
    username: str = "Emby"
    avatar_url: str = ""


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8087
    webhook_path: str = "/webhook/emby"
    shared_secret: str = ""


@dataclass
class NotificationConfig:
    playback_start: bool = True
    playback_pause: bool = False
    playback_unpause: bool = False
    playback_stop: bool = True
    library_new: bool = True
    mark_played: bool = False
    mark_unplayed: bool = False
    include_summary: bool = True
    include_progress_bar: bool = True
    include_poster: bool = True
    include_provider_links: bool = True
    ignore_users: list = field(default_factory=list)
    ignore_libraries: list = field(default_factory=list)
    # Emby often fires "library.new" twice in quick succession for the same
    # item (once when the file is detected, again moments later once
    # metadata/artwork finishes downloading). Suppress a repeat notification
    # for the same item within this window. Keep this short -- the "new item
    # added" webhook is often fired against the *series*, not the specific
    # episode, so a long window can wrongly suppress a genuinely new episode
    # added later. Set to 0 to disable deduping.
    library_new_dedupe_minutes: float = 15


@dataclass
class DatabaseConfig:
    path: str = "embytulli.db"
    enabled: bool = True


@dataclass
class AppConfig:
    emby: EmbyConfig
    discord: DiscordConfig
    server: ServerConfig
    notifications: NotificationConfig
    database: DatabaseConfig
    log_level: str = "INFO"


class ConfigError(Exception):
    pass


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found at {config_path}. "
            f"Copy config.example.yaml to config.yaml and fill in your values."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    emby_raw = raw.get("emby", {})
    discord_raw = raw.get("discord", {})
    server_raw = raw.get("server", {})
    notif_raw = raw.get("notifications", {})
    db_raw = raw.get("database", {})
    log_raw = raw.get("logging", {})

    base_url = os.environ.get("EMBYTULLI_EMBY_BASE_URL", emby_raw.get("base_url", "")).rstrip("/")
    api_key = os.environ.get("EMBYTULLI_EMBY_API_KEY", emby_raw.get("api_key", ""))
    webhook_url = os.environ.get("EMBYTULLI_DISCORD_WEBHOOK_URL", discord_raw.get("webhook_url", ""))
    port = int(os.environ.get("EMBYTULLI_SERVER_PORT", server_raw.get("port", 8087)))
    shared_secret = os.environ.get("EMBYTULLI_SHARED_SECRET", server_raw.get("shared_secret", ""))

    if not base_url:
        raise ConfigError("emby.base_url is required in config.yaml")
    if not webhook_url or "discord.com/api/webhooks" not in webhook_url:
        raise ConfigError(
            "discord.webhook_url is required and must be a valid Discord webhook URL"
        )

    cfg = AppConfig(
        emby=EmbyConfig(base_url=base_url, api_key=api_key),
        discord=DiscordConfig(
            webhook_url=webhook_url,
            username=discord_raw.get("username", "Emby"),
            avatar_url=discord_raw.get("avatar_url", ""),
        ),
        server=ServerConfig(
            host=server_raw.get("host", "0.0.0.0"),
            port=port,
            webhook_path=server_raw.get("webhook_path", "/webhook/emby"),
            shared_secret=shared_secret,
        ),
        notifications=NotificationConfig(
            playback_start=notif_raw.get("playback_start", True),
            playback_pause=notif_raw.get("playback_pause", False),
            playback_unpause=notif_raw.get("playback_unpause", False),
            playback_stop=notif_raw.get("playback_stop", True),
            library_new=notif_raw.get("library_new", True),
            mark_played=notif_raw.get("mark_played", False),
            mark_unplayed=notif_raw.get("mark_unplayed", False),
            include_summary=notif_raw.get("include_summary", True),
            include_progress_bar=notif_raw.get("include_progress_bar", True),
            include_poster=notif_raw.get("include_poster", True),
            include_provider_links=notif_raw.get("include_provider_links", True),
            ignore_users=[u.lower() for u in notif_raw.get("ignore_users", [])],
            ignore_libraries=[l.lower() for l in notif_raw.get("ignore_libraries", [])],
            library_new_dedupe_minutes=notif_raw.get("library_new_dedupe_minutes", 15),
        ),
        database=DatabaseConfig(
            path=db_raw.get("path", "embytulli.db"),
            enabled=db_raw.get("enabled", True),
        ),
        log_level=log_raw.get("level", "INFO"),
    )
    return cfg


if __name__ == "__main__":
    try:
        cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
        print("Config OK.")
        print(f"  Emby base URL: {cfg.emby.base_url}")
        print(f"  Discord webhook: {cfg.discord.webhook_url[:40]}...")
        print(f"  Listening on: {cfg.server.host}:{cfg.server.port}{cfg.server.webhook_path}")
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)
