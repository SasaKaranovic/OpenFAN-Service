"""Notifications through Apprise (Telegram, Discord, ntfy, e-mail, Pushover, Gotify, Slack ...).

Messages are queued and sent from a background task so a slow or unreachable
notification service never delays fan control.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from .models import Notifications

log = logging.getLogger("openfan.notify")

SEND_TIMEOUT = 30.0


def _send_blocking(urls: list[str], title: str, body: str) -> tuple[bool, str]:
    try:
        import apprise
    except ImportError:
        return False, "the 'apprise' package is not installed"
    app = apprise.Apprise()
    bad = [u for u in urls if not app.add(u)]
    if bad:
        return False, f"invalid notification URL(s): {', '.join(_mask(u) for u in bad)}"
    if not urls:
        return False, "no notification URLs configured"
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
    try:
        ok = app.notify(title=title, body=body)
    finally:
        socket.setdefaulttimeout(old)
    return (True, "sent") if ok else (False, "at least one service rejected the message (see the log)")


def _mask(url: str) -> str:
    scheme, _, rest = url.partition("://")
    return f"{scheme}://…" if rest else url


class Notifier:
    def __init__(self) -> None:
        self.cfg = Notifications()
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=50)
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.on_error = None  # callback(message)

    def configure(self, cfg: Notifications) -> None:
        self.cfg = cfg

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._worker(), name="notifier")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def wants(self, kind: str) -> bool:
        if not self.cfg.enabled or not self.cfg.urls:
            return False
        return {"failsafe": self.cfg.on_failsafe, "recovery": self.cfg.on_recovery,
                "controller": self.cfg.on_controller}.get(kind, True)

    def notify(self, kind: str, title: str, body: str) -> None:
        if not self.wants(kind):
            return
        try:
            self._queue.put_nowait((title, body))
        except asyncio.QueueFull:
            log.warning("Notification queue full, dropping: %s", title)

    async def send_now(self, title: str, body: str, urls: list[str] | None = None) -> tuple[bool, str]:
        urls = list(urls if urls is not None else self.cfg.urls)
        try:
            return await asyncio.wait_for(asyncio.to_thread(_send_blocking, urls, title, body), SEND_TIMEOUT + 5)
        except asyncio.TimeoutError:
            return False, "timed out"
        except Exception as exc:  # apprise plugins can raise anything
            return False, str(exc)

    async def _worker(self) -> None:
        while True:
            title, body = await self._queue.get()
            ok, msg = await self.send_now(title, body)
            if not ok:
                self.last_error = msg
                log.warning("Notification failed: %s", msg)
                if self.on_error:
                    self.on_error(msg)
            else:
                self.last_error = None

    def status(self) -> dict[str, Any]:
        return {"enabled": self.cfg.enabled, "configured": len(self.cfg.urls), "last_error": self.last_error}
