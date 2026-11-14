"""Keeps one connection per configured controller and reconnects on failure."""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..models import Controller
from .base import ControllerDriver, ControllerError, ControllerInfo
from .drivers import BUILTIN_DRIVERS

log = logging.getLogger("openfan.controller")

DRIVERS: dict[str, type[ControllerDriver]] = {d.type_name: d for d in BUILTIN_DRIVERS}

CALL_TIMEOUT = 5.0
CONNECT_TIMEOUT = 10.0


@dataclass
class ControllerRuntime:
    cfg: Controller
    driver: ControllerDriver | None = None
    executor: ThreadPoolExecutor | None = None
    connected: bool = False
    info: ControllerInfo | None = None
    error: str | None = None
    rpm: dict[int, int] = field(default_factory=dict)
    sent: dict[int, tuple[int, float]] = field(default_factory=dict)
    next_retry: float = 0.0
    failures: int = 0
    connected_since: float | None = None
    disconnected_since: float | None = None

    @property
    def target(self) -> str:
        if self.driver:
            return self.driver.target
        return str(self.cfg.options.get("port") or self.cfg.options.get("url") or "")


class ControllerManager:
    def __init__(self) -> None:
        self.ctrls: dict[str, ControllerRuntime] = {}
        #: called with (controller_id, connected: bool, message)
        self.on_status = None

    def apply(self, controllers: list[Controller]) -> None:
        wanted = {c.id: c for c in controllers if c.enabled}
        for cid in list(self.ctrls):
            if cid not in wanted or wanted[cid].model_dump() != self.ctrls[cid].cfg.model_dump():
                self._drop(cid)
        for cid, cfg in wanted.items():
            if cid not in self.ctrls:
                rt = ControllerRuntime(cfg=cfg, disconnected_since=time.monotonic())
                cls = DRIVERS.get(cfg.type)
                if cls is None:
                    rt.error = f"unknown controller type '{cfg.type}'"
                else:
                    try:
                        rt.driver = cls(cfg.id, cfg.options)
                        rt.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ctrl-{cfg.id}")
                    except Exception as exc:
                        rt.error = f"invalid options: {exc}"
                self.ctrls[cid] = rt

    def _drop(self, cid: str) -> None:
        rt = self.ctrls.pop(cid, None)
        if rt and rt.driver and rt.executor:
            rt.executor.submit(rt.driver.close)
            rt.executor.shutdown(wait=False)

    async def _call(self, rt: ControllerRuntime, fn, *args, timeout: float = CALL_TIMEOUT):
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(rt.executor, fn, *args), timeout)
        except asyncio.TimeoutError as exc:
            # The worker thread may be stuck in I/O. Abandon it and use a fresh one.
            old = rt.executor
            rt.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ctrl-{rt.cfg.id}")
            old.shutdown(wait=False)
            raise ControllerError(f"no answer within {timeout:.0f} s") from exc

    def _mark_down(self, rt: ControllerRuntime, message: str) -> None:
        was_up = rt.connected
        rt.connected = False
        rt.error = message
        rt.failures += 1
        rt.next_retry = time.monotonic() + min(30.0, 2.0 ** min(rt.failures, 5))
        rt.sent.clear()
        if was_up:
            rt.disconnected_since = time.monotonic()
            rt.connected_since = None
            log.warning("Controller %s disconnected: %s", rt.cfg.id, message)
            if self.on_status:
                self.on_status(rt.cfg.id, False, message)
        if rt.driver and rt.executor:
            rt.executor.submit(rt.driver.close)

    async def ensure_connected(self) -> None:
        """(Re)connect every controller that is down, all at once so one slow board never delays another."""
        now = time.monotonic()
        due = [rt for rt in self.ctrls.values()
               if not rt.connected and rt.driver is not None and now >= rt.next_retry]
        if due:
            await asyncio.gather(*(self._connect(rt) for rt in due))

    async def _connect(self, rt: ControllerRuntime) -> None:
        try:
            rt.info = await self._call(rt, rt.driver.connect, timeout=CONNECT_TIMEOUT)
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            if msg != rt.error:
                log.warning("Controller %s: %s", rt.cfg.id, msg)
            self._mark_down(rt, msg)
            return
        rt.connected, rt.error, rt.failures = True, None, 0
        rt.connected_since, rt.disconnected_since = time.monotonic(), None
        log.info("Controller %s connected: %s with %d fans (%s)", rt.cfg.id, rt.info.name,
                 rt.info.fan_count, rt.target)
        if self.on_status:
            self.on_status(rt.cfg.id, True, f"{rt.info.name} on {rt.target}")

    async def set_pwm(self, cid: str, index: int, pwm: int, keepalive: float) -> bool:
        rt = self.ctrls.get(cid)
        if not rt or not rt.connected:
            return False
        last = rt.sent.get(index)
        now = time.monotonic()
        if last and last[0] == pwm and now - last[1] < keepalive:
            return True
        try:
            await self._call(rt, rt.driver.set_pwm, index, pwm)
        except Exception as exc:
            self._mark_down(rt, str(exc) or exc.__class__.__name__)
            return False
        rt.sent[index] = (pwm, now)
        return True

    async def read_rpm(self, cid: str) -> None:
        rt = self.ctrls.get(cid)
        if not rt or not rt.connected:
            return
        try:
            rt.rpm = await self._call(rt, rt.driver.read_rpm)
        except Exception as exc:
            self._mark_down(rt, str(exc) or exc.__class__.__name__)

    async def read_fan_names(self, cid: str) -> dict[int, str]:
        rt = self.ctrls.get(cid)
        if rt is None or rt.driver is None:
            raise ControllerError(f"controller '{cid}' is not configured or disabled")
        if not rt.driver.supports_fan_names:
            raise ControllerError("fan names can only be read from a controller connected over the REST API")
        if not rt.connected:
            raise ControllerError(f"controller '{rt.cfg.name}' is not connected: {rt.error or 'connecting'}")
        return await self._call(rt, rt.driver.read_fan_names)

    async def close(self, final_pwm: dict[str, dict[int, int]] | None = None) -> None:
        """Optionally set final speeds, then close every connection."""
        for cid, rt in list(self.ctrls.items()):
            if final_pwm and rt.connected:
                for idx, pwm in final_pwm.get(cid, {}).items():
                    try:
                        await self._call(rt, rt.driver.set_pwm, idx, pwm, timeout=2.0)
                    except Exception:
                        log.warning("Could not set fan %s:%s to %s%% while stopping", cid, idx, pwm)
                        break
            self._drop(cid)

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        out = []
        for cid, rt in self.ctrls.items():
            out.append({
                "id": cid, "name": rt.cfg.name, "type": rt.cfg.type, "target": rt.target,
                "supports_fan_names": bool(rt.driver and rt.driver.supports_fan_names),
                "connected": rt.connected, "error": rt.error,
                "board": rt.info.name if rt.info else None,
                "fan_count": rt.info.fan_count if rt.info else None,
                "hardware": rt.info.hardware if rt.info else None,
                "firmware": rt.info.firmware if rt.info else None,
                "down_for": round(now - rt.disconnected_since, 1) if rt.disconnected_since and not rt.connected else None,
            })
        return out
