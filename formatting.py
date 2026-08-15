"""Small presentation helpers: progress bars, durations, quality strings."""
from __future__ import annotations

from event_parser import EmbyEvent

TICKS_PER_SECOND = 10_000_000

_RESOLUTION_LABELS = [
    (3800, "4K"),
    (2500, "1440p"),
    (1900, "1080p"),
    (1260, "720p"),
    (850, "480p"),
]


def ticks_to_seconds(ticks: int | None) -> int | None:
    if ticks is None:
        return None
    return int(ticks / TICKS_PER_SECOND)


def format_duration(ticks: int | None) -> str | None:
    seconds = ticks_to_seconds(ticks)
    if seconds is None:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m {s}s" if s else f"{m}m"
    return f"{s}s"


def resolution_label(width: int | None) -> str | None:
    if not width:
        return None
    for min_width, label in _RESOLUTION_LABELS:
        if width >= min_width:
            return label
    return f"{width}px"


def quality_string(event: EmbyEvent) -> str | None:
    m = event.media
    parts = []
    if m.video_width:
        res = resolution_label(m.video_width)
        video_bits = [res] if res else []
        if m.video_codec:
            video_bits.append(m.video_codec.upper())
        if m.video_range and m.video_range.upper() not in ("SDR", ""):
            video_bits.append(m.video_range.upper())
        if video_bits:
            parts.append(" ".join(video_bits))
    if m.audio_codec:
        audio_bits = [m.audio_codec.upper()]
        if m.audio_channels:
            channel_map = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}
            audio_bits.append(channel_map.get(m.audio_channels, f"{m.audio_channels}ch"))
        parts.append(" ".join(audio_bits))
    return " / ".join(parts) if parts else None


def progress_bar(fraction: float | None, width: int = 20) -> str | None:
    if fraction is None:
        return None
    filled = round(fraction * width)
    filled = max(0, min(width, filled))
    bar = "▓" * filled + "░" * (width - filled)
    return f"`{bar}` {fraction * 100:.0f}%"


# Series-shaped items get the /tv/ path on TMDb; everything else (Movie) gets /movie/.
_SERIES_LIKE_TYPES = {"Series", "Season", "Episode"}


def provider_links(event: EmbyEvent) -> list[tuple[str, str]]:
    """Builds (label, url) pairs from whatever provider IDs Emby's metadata
    scan attached to the item. TMDb/IMDb/TVDB are populated by Emby's default
    metadata providers; TVMaze only shows up if the community TVMaze plugin
    is installed and set as a metadata source. Any ID that isn't present is
    silently skipped -- nothing here assumes a specific provider is set up."""
    ids = event.provider_ids or {}
    links: list[tuple[str, str]] = []
    is_series_like = event.item_type in _SERIES_LIKE_TYPES

    tmdb_id = ids.get("tmdb")
    if tmdb_id:
        path = "tv" if is_series_like else "movie"
        links.append(("TMDb", f"https://www.themoviedb.org/{path}/{tmdb_id}"))

    imdb_id = ids.get("imdb")
    if imdb_id:
        links.append(("IMDb", f"https://www.imdb.com/title/{imdb_id}/"))

    tvdb_id = ids.get("tvdb")
    if tvdb_id and is_series_like:
        links.append(("TheTVDB", f"https://www.thetvdb.com/?tab=series&id={tvdb_id}"))

    tvmaze_id = ids.get("tvmaze")
    if tvmaze_id and is_series_like:
        links.append(("TVmaze", f"https://www.tvmaze.com/shows/{tvmaze_id}"))

    return links


def provider_links_markdown(event: EmbyEvent) -> str | None:
    links = provider_links(event)
    if not links:
        return None
    return " • ".join(f"[{label}]({url})" for label, url in links)
