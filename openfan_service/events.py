"""In-memory event log shown on the dashboard (also persisted to the history DB)."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

log = logging.getLogger("openfan.events")


class EventLog:
    def __init__(self, maxlen: int = 300) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._sinks: list[Callable[[dict[str, Any]], None]] = []

    def add_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        self._sinks.append(sink)

    def preload(self, items: list[dict[str, Any]]) -> None:
        for item in reversed(items):
            self._items.appendleft(item)

    def add(self, level: str, kind: str, message: str) -> dict[str, Any]:
        item = {"ts": time.time(), "level": level, "kind": kind, "message": message}
        self._items.append(item)
        getattr(log, "warning" if level in ("error", "warning") else "info")("[%s] %s", kind, message)
        for sink in self._sinks:
            try:
                sink(item)
            except Exception:
                log.debug("event sink failed", exc_info=True)
        return item

    def clear(self) -> None:
        """Forget all events (the dashboard's "Clear" button). Does not affect fan control."""
        self._items.clear()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._items)[-limit:][::-1]
