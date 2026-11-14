"""Temperature / fan history in SQLite (``/config/history.sqlite``).

History is a nice-to-have. Every error here is caught and reported; it can
never stop or slow down fan control.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

log = logging.getLogger("openfan.history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (ts INTEGER NOT NULL, key TEXT NOT NULL, v REAL);
CREATE INDEX IF NOT EXISTS samples_key_ts ON samples (key, ts);
CREATE TABLE IF NOT EXISTS events (ts REAL NOT NULL, level TEXT, kind TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


class History:
    def __init__(self, path: Path, retention_days: int = 7) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self.error: str | None = None
        self._db: sqlite3.Connection | None = None
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history")
        self._last_prune = 0.0

    # -- sync helpers (run in the history thread) ------------------------------------
    def _open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            try:
                mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            except sqlite3.DatabaseError:
                mode = "unknown"
            if str(mode).lower() != "wal":
                # Network shares (SMB/NFS) cannot do WAL; the classic journal is safer there.
                db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(SCHEMA)
            db.commit()
            self._db = db
            self.error = None
        except sqlite3.DatabaseError as exc:
            self.error = f"history database unavailable: {exc}"
            log.error(self.error)
            self._db = None

    def _write(self, rows: list[tuple[int, str, float | None]]) -> None:
        if self._db is None:
            return
        try:
            self._db.executemany("INSERT INTO samples (ts, key, v) VALUES (?, ?, ?)", rows)
            self._db.commit()
            now = time.time()
            if now - self._last_prune > 3600:
                self._last_prune = now
                cutoff = int(now - self.retention_days * 86400)
                self._db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
                self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
                self._db.commit()
            self.error = None
        except sqlite3.DatabaseError as exc:
            self.error = f"history write failed: {exc}"
            log.warning(self.error)

    def _add_event(self, ev: dict[str, Any]) -> None:
        if self._db is None:
            return
        try:
            self._db.execute("INSERT INTO events (ts, level, kind, message) VALUES (?, ?, ?, ?)",
                             (ev["ts"], ev["level"], ev["kind"], ev["message"]))
            self._db.commit()
        except sqlite3.DatabaseError:
            pass

    def _clear_events(self) -> None:
        if self._db is None:
            return
        try:
            self._db.execute("DELETE FROM events")
            self._db.commit()
        except sqlite3.DatabaseError as exc:
            log.warning("could not clear events: %s", exc)

    def _events(self, limit: int) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        try:
            rows = self._db.execute("SELECT ts, level, kind, message FROM events ORDER BY ts DESC LIMIT ?",
                                    (limit,)).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [{"ts": r[0], "level": r[1], "kind": r[2], "message": r[3]} for r in rows]

    def _query(self, keys: list[str], since: float, until: float, points: int) -> dict[str, list]:
        out: dict[str, list] = {k: [] for k in keys}
        if self._db is None or not keys:
            return out
        bucket = max(1, int((until - since) / max(10, points)))
        q = (f"SELECT key, (ts / {bucket}) * {bucket} AS b, AVG(v), MAX(v) FROM samples "
             f"WHERE key IN ({','.join('?' * len(keys))}) AND ts >= ? AND ts <= ? GROUP BY key, b ORDER BY b")
        try:
            for key, b, avg, mx in self._db.execute(q, (*keys, int(since), int(until))):
                # Fail-safe flags use MAX so a short fail-safe is never averaged away.
                val = mx if key.endswith(":fs") else avg
                out[key].append([b, None if val is None else round(val, 2)])
        except sqlite3.DatabaseError as exc:
            log.warning("history query failed: %s", exc)
        return out

    # -- async API ---------------------------------------------------------------
    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._exec, fn, *args)

    async def open(self) -> None:
        await self._run(self._open)

    async def write(self, rows: list[tuple[int, str, float | None]]) -> None:
        if rows:
            await self._run(self._write, rows)

    def add_event_nowait(self, ev: dict[str, Any]) -> None:
        self._exec.submit(self._add_event, ev)

    async def clear_events(self) -> None:
        await self._run(self._clear_events)

    async def events(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._run(self._events, limit)

    async def query(self, keys: list[str], since: float, until: float, points: int = 300) -> dict[str, list]:
        return await self._run(self._query, keys, since, until, points)

    async def close(self) -> None:
        def _close():
            if self._db is not None:
                self._db.close()
                self._db = None
        await self._run(_close)
        self._exec.shutdown(wait=False)
