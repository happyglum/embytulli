"""Normalizes raw Emby webhook payloads into a stable EmbyEvent object.

Emby's built-in Webhooks feature has shipped a few different payload shapes
across server versions (some nest playback info under "Session", some put
user info under "User", some flatten fields at the top level, some use
"Event" and some use "NotificationType" for the event name). Rather than
betting on one exact schema, every field below is looked up through a list
of possible paths and the first one that resolves wins. If Emby changes
something, add another candidate path here -- nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _get_path(data: dict, path: str) -> Any:
    """Resolve a dotted path like 'Item.Name' or 'Session.PlayState.IsPaused'."""
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _first(data: dict, *paths: str) -> Any:
    for p in paths:
        val = _get_path(data, p)
        if val is not None and val != "":
            return val
    return None


# Normalized event type constants used everywhere else in the app.
PLAYBACK_START = "playback.start"
PLAYBACK_PAUSE = "playback.pause"
PLAYBACK_UNPAUSE = "playback.unpause"
PLAYBACK_STOP = "playback.stop"
LIBRARY_NEW = "library.new"
MARK_PLAYED = "item.markplayed"
MARK_UNPLAYED = "item.markunplayed"
TEST_EVENT = "system.webhooktest"
UNKNOWN = "unknown"

_EVENT_ALIASES = {
    "playback.start": PLAYBACK_START,
    "playbackstart": PLAYBACK_START,
    "playback.pause": PLAYBACK_PAUSE,
    "playbackpause": PLAYBACK_PAUSE,
    "playback.unpause": PLAYBACK_UNPAUSE,
    "playbackunpause": PLAYBACK_UNPAUSE,
    "playback.stop": PLAYBACK_STOP,
    "playbackstop": PLAYBACK_STOP,
    "library.new": LIBRARY_NEW,
    "library.newmediaadded": LIBRARY_NEW,
    "item.rate": MARK_PLAYED,  # favourite/rate handled generically, mapped separately below
    "item.markplayed": MARK_PLAYED,
    "item.markunplayed": MARK_UNPLAYED,
    "system.webhooktest": TEST_EVENT,
    "systemwebhooktest": TEST_EVENT,
}


def normalize_event_type(raw: str) -> str:
    if not raw:
        return UNKNOWN
    key = str(raw).strip().lower()
    return _EVENT_ALIASES.get(key, key if key in _EVENT_ALIASES.values() else UNKNOWN)


@dataclass
class MediaStreamInfo:
    video_codec: Optional[str] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    video_range: Optional[str] = None  # SDR / HDR10 / Dolby Vision etc.
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None


@dataclass
class EmbyEvent:
    event_type: str
    raw_event: str
    item_name: Optional[str] = None
    item_type: Optional[str] = None  # Movie, Episode, Audio, Series, ...
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    year: Optional[int] = None
    overview: Optional[str] = None
    runtime_ticks: Optional[int] = None
    position_ticks: Optional[int] = None
    item_id: Optional[str] = None
    library_name: Optional[str] = None

    user_name: Optional[str] = None
    device_name: Optional[str] = None
    client_name: Optional[str] = None

    server_name: Optional[str] = None
    server_id: Optional[str] = None

    is_paused: bool = False
    media: MediaStreamInfo = field(default_factory=MediaStreamInfo)
    # Lowercased provider name -> id, e.g. {"tmdb": "1396", "imdb": "tt0903747"}.
    # Populated from Item.ProviderIds if Emby's metadata scan filled it in.
    provider_ids: dict = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        if self.item_type == "Episode" and self.series_name:
            se = f"S{self.season_number:02d}" if self.season_number is not None else ""
            ep = f"E{self.episode_number:02d}" if self.episode_number is not None else ""
            code = f"{se}{ep}"
            title = f"{self.series_name}"
            if code:
                title += f" - {code}"
            if self.item_name:
                title += f" - {self.item_name}"
            return title
        if self.item_name and self.year:
            return f"{self.item_name} ({self.year})"
        return self.item_name or "Unknown title"

    @property
    def progress_fraction(self) -> Optional[float]:
        if not self.runtime_ticks or self.position_ticks is None:
            return None
        if self.runtime_ticks <= 0:
            return None
        return max(0.0, min(1.0, self.position_ticks / self.runtime_ticks))


def _parse_provider_ids(item: dict) -> dict:
    """Normalize Item.ProviderIds (e.g. {"Tmdb": "1396", "Imdb": "tt0903747"})
    into lowercase keys so callers don't need to guess Emby's casing."""
    raw = item.get("ProviderIds") if isinstance(item, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in raw.items() if v}


def _parse_media_streams(item: dict) -> MediaStreamInfo:
    info = MediaStreamInfo()
    streams = item.get("MediaStreams") or []
    for s in streams:
        if not isinstance(s, dict):
            continue
        stype = s.get("Type")
        if stype == "Video" and info.video_codec is None:
            info.video_codec = s.get("Codec")
            info.video_width = s.get("Width")
            info.video_height = s.get("Height")
            info.video_range = s.get("VideoRange") or s.get("VideoRangeType")
        elif stype == "Audio" and info.audio_codec is None:
            info.audio_codec = s.get("Codec")
            info.audio_channels = s.get("Channels")
            info.audio_language = s.get("DisplayLanguage") or s.get("Language")
    return info


def parse_webhook_payload(payload: dict) -> EmbyEvent:
    """Turn a raw Emby webhook JSON body into an EmbyEvent."""
    raw_event_type = _first(payload, "Event", "NotificationType", "notification_type") or ""
    event_type = normalize_event_type(raw_event_type)

    item = payload.get("Item") if isinstance(payload.get("Item"), dict) else {}
    session = payload.get("Session") if isinstance(payload.get("Session"), dict) else {}
    user = payload.get("User") if isinstance(payload.get("User"), dict) else {}
    server = payload.get("Server") if isinstance(payload.get("Server"), dict) else {}
    play_state = session.get("PlayState") if isinstance(session.get("PlayState"), dict) else {}

    item_name = _first(payload, "Item.Name", "Name", "ItemName")
    item_type = _first(payload, "Item.Type", "ItemType")
    series_name = _first(payload, "Item.SeriesName", "SeriesName")
    season_number = _first(payload, "Item.ParentIndexNumber", "Item.SeasonNumber", "SeasonNumber")
    episode_number = _first(payload, "Item.IndexNumber", "EpisodeNumber")
    year = _first(payload, "Item.ProductionYear", "Year")
    overview = _first(payload, "Item.Overview", "Overview")
    runtime_ticks = _first(payload, "Item.RunTimeTicks", "RunTimeTicks")
    position_ticks = _first(
        payload,
        "PlaybackPositionTicks",
        "Session.PlayState.PositionTicks",
        "PlayState.PositionTicks",
        "PositionTicks",
    )
    item_id = _first(payload, "Item.Id", "ItemId", "Id")
    library_name = _first(payload, "Item.Library.Name", "LibraryName")

    user_name = _first(
        payload,
        "NotificationUsername",
        "User.Name",
        "Session.UserName",
        "UserName",
    )
    device_name = _first(payload, "Session.DeviceName", "DeviceName")
    client_name = _first(payload, "Session.Client", "Client", "ClientName")

    server_name = _first(payload, "Server.Name", "ServerName")
    server_id = _first(payload, "Server.Id", "ServerId")

    is_paused = bool(_first(payload, "Session.PlayState.IsPaused", "IsPaused") or False)

    media = _parse_media_streams(item) if item else MediaStreamInfo()
    provider_ids = _parse_provider_ids(item) if item else {}

    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return EmbyEvent(
        event_type=event_type,
        raw_event=str(raw_event_type),
        item_name=item_name,
        item_type=item_type,
        series_name=series_name,
        season_number=_to_int(season_number),
        episode_number=_to_int(episode_number),
        year=_to_int(year),
        overview=overview,
        runtime_ticks=_to_int(runtime_ticks),
        position_ticks=_to_int(position_ticks),
        item_id=str(item_id) if item_id is not None else None,
        library_name=library_name,
        user_name=user_name,
        device_name=device_name,
        client_name=client_name,
        server_name=server_name,
        server_id=str(server_id) if server_id is not None else None,
        is_paused=is_paused,
        media=media,
        provider_ids=provider_ids,
    )
