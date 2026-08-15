"""Minimal SQLite event log.

Not required for Discord notifications to work. It exists so every event
that flows through this app is durably recorded now, which is the
foundation a future analytics/dashboard phase (watch history, top users,
top titles, etc. -- the Tautulli "Graphs" equivalent) will read from,
without having to backfill anything later.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from event_parser import EmbyEvent

logger = logging.getLogger("embytulli.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    raw_event TEXT,
    item_name TEXT,
    item_type TEXT,
    series_name TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    year INTEGER,
    item_id TEXT,
    library_name TEXT,
    user_name TEXT,
    device_name TEXT,
    client_name TEXT,
    runtime_ticks INTEGER,
    position_ticks INTEGER,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_name);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


class EventDB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def log_event(self, event: EmbyEvent, notified: bool) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, raw_event, item_name, item_type, series_name,
                        season_number, episode_number, year, item_id, library_name,
                        user_name, device_name, client_name, runtime_ticks,
                        position_ticks, notified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_type,
                        event.raw_event,
                        event.item_name,
                        event.item_type,
                        event.series_name,
                        event.season_number,
                        event.episode_number,
                        event.year,
                        event.item_id,
                        event.library_name,
                        event.user_name,
                        event.device_name,
                        event.client_name,
                        event.runtime_ticks,
                        event.position_ticks,
                        1 if notified else 0,
                    ),
                )
        except sqlite3.Error as e:
            logger.error("Failed to log event to database: %s", e)

    def was_recently_notified(self, item_id: str, event_type: str, within_minutes: float) -> bool:
        """True if a notification for this exact item_id + event_type was already
        sent within the last `within_minutes`. Used to dedupe Emby's tendency to
        fire the same 'library.new' webhook twice in quick succession for one
        item (once on file detection, again moments later once metadata/artwork
        finishes downloading).

        Deliberately a short window: Emby's "new item added" webhook is often
        fired against the *series* item rather than the specific episode, so a
        genuinely new episode added later can carry the same item_id as an
        earlier one. Keeping this window short (minutes, not hours) means that
        only true back-to-back duplicate fires get suppressed, not later,
        separate additions to the same series."""
        if not item_id or within_minutes <= 0:
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT 1 FROM events
                    WHERE item_id = ?
                      AND event_type = ?
                      AND notified = 1
                      AND received_at >= datetime('now', ?)
                    LIMIT 1
                    """,
                    (item_id, event_type, f"-{within_minutes} minutes"),
                ).fetchone()
                return row is not None
        except sqlite3.Error as e:
            logger.error("Failed to check recent notifications: %s", e)
            return False
