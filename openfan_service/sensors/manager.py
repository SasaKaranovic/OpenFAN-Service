"""Runs every configured sensor source in its own task and keeps the latest readings."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..models import SensorSource
from .base import ERROR, OK, SLEEPING, Reading, SensorPlugin, SensorState
from .registry import PluginRegistry

log = logging.getLogger("openfan.sensors")


@dataclass
class SourceRuntime:
    cfg: SensorSource
    plugin: SensorPlugin | None = None
    task: asyncio.Task | None = None
    error: str | None = None
    polled_once: bool = False
    last_poll: float | None = None
    busy: bool = False
    sensors: set[str] = field(default_factory=set)


class SensorManager:
    def __init__(self, registry: PluginRegistry, stale_timeout: float = 60.0) -> None:
        self.registry = registry
        self.stale_timeout = stale_timeout
        self.sources: dict[str, SourceRuntime] = {}
        self.states: dict[str, SensorState] = {}
        self._started = time.monotonic()

    # -- lifecycle -----------------------------------------------------------------
    def apply(self, sources: list[SensorSource]) -> None:
        """Start, restart or stop sources so they match ``sources``."""
        wanted = {s.id: s for s in sources if s.enabled}
        for sid in list(self.sources):
            rt = self.sources[sid]
            if sid not in wanted or wanted[sid].model_dump() != rt.cfg.model_dump():
                self._stop(sid)
        for sid, cfg in wanted.items():
            if sid not in self.sources:
                self._start(cfg)
        # Forget sensors of sources that no longer exist (disabled or deleted).
        for ref in [r for r, st in self.states.items() if st.source_id not in self.sources]:
            del self.states[ref]

    def _start(self, cfg: SensorSource) -> None:
        rt = SourceRuntime(cfg=cfg)
        self.sources[cfg.id] = rt
        cls = self.registry.get(cfg.type)
        if cls is None:
            rt.error = f"unknown plugin type '{cfg.type}'"
            log.error("Source %s: %s", cfg.id, rt.error)
            return
        try:
            rt.plugin = cls(cfg.id, cfg.options)
        except Exception as exc:
            rt.error = f"invalid options: {exc}"
            log.error("Source %s: %s", cfg.id, rt.error)
            return
        if not cls.push:
            rt.task = asyncio.get_running_loop().create_task(self._poll_loop(rt), name=f"source-{cfg.id}")
        log.info("Started sensor source %s (%s)", cfg.id, cfg.type)

    def _stop(self, sid: str) -> None:
        rt = self.sources.pop(sid, None)
        if not rt:
            return
        if rt.task:
            rt.task.cancel()
        if rt.plugin:
            try:
                rt.plugin.close()
            except Exception:
                log.exception("Closing source %s failed", sid)

    async def stop(self) -> None:
        for sid in list(self.sources):
            self._stop(sid)

    # -- polling -------------------------------------------------------------------
    async def _poll_loop(self, rt: SourceRuntime) -> None:
        interval = rt.cfg.poll_interval
        timeout = max(10.0, interval * 3)
        while True:
            if not rt.busy:
                rt.busy = True
                try:
                    fut = asyncio.ensure_future(asyncio.to_thread(rt.plugin.read))
                    readings = await asyncio.wait_for(asyncio.shield(fut), timeout)
                    self._update(rt, readings)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    self._fail(rt, f"no answer within {int(timeout)} s")
                    # The worker thread may still be stuck; only start a new read once it ends.
                    fut.add_done_callback(lambda _f, r=rt: setattr(r, "busy", False))
                    await asyncio.sleep(interval)
                    continue
                except Exception as exc:
                    self._fail(rt, str(exc) or exc.__class__.__name__)
                rt.busy = False
            await asyncio.sleep(interval)

    def _fail(self, rt: SourceRuntime, message: str) -> None:
        if rt.error != message:
            log.warning("Source %s failed: %s", rt.cfg.id, message)
        rt.error = message
        rt.polled_once = True
        rt.last_poll = time.monotonic()
        for ref in rt.sensors:
            st = self.states.get(ref)
            if st:
                st.status, st.message = ERROR, f"source error: {message}"

    def _update(self, rt: SourceRuntime, readings: dict[str, Reading]) -> None:
        now, wall = time.monotonic(), time.time()
        if rt.error:
            log.info("Source %s recovered", rt.cfg.id)
        rt.error = None
        rt.polled_once = True
        rt.last_poll = now
        seen = set()
        for sensor_id, reading in readings.items():
            ref = f"{rt.cfg.id}:{sensor_id}"
            seen.add(ref)
            st = self.states.get(ref)
            if st is None:
                st = self.states[ref] = SensorState(ref=ref, source_id=rt.cfg.id, sensor_id=sensor_id)
            st.label = reading.label
            st.unit = reading.unit
            st.message = reading.message
            st.status = reading.status
            if reading.status in (OK, SLEEPING):
                st.value = reading.value
                st.last_ok = now
                st.last_seen_wall = wall
        for ref in rt.sensors - seen:
            st = self.states.get(ref)
            if st:
                st.status, st.message = ERROR, "no longer reported by the source"
        rt.sensors |= seen

    def push(self, source_id: str, readings: dict[str, Reading]) -> int:
        rt = self.sources.get(source_id)
        if rt is None or rt.plugin is None or not rt.plugin.push:
            raise KeyError(source_id)
        # Push sources only update the sensors they send; absent ones go stale on their own.
        now, wall = time.monotonic(), time.time()
        rt.polled_once, rt.last_poll, rt.error = True, now, None
        for sensor_id, reading in readings.items():
            ref = f"{source_id}:{sensor_id}"
            st = self.states.setdefault(ref, SensorState(ref=ref, source_id=source_id, sensor_id=sensor_id))
            st.status, st.message, st.label = reading.status, reading.message, reading.label or st.label
            if reading.status in (OK, SLEEPING):
                st.value, st.last_ok, st.last_seen_wall = reading.value, now, wall
            rt.sensors.add(ref)
        return len(readings)

    # -- queries -------------------------------------------------------------------
    def effective(self, ref: str, now: float | None = None) -> tuple[str, float | None, str | None]:
        """Return (status, value, message) for a sensor, with staleness applied.

        status is one of ok, sleeping, stale, error, missing, pending.
        """
        now = now or time.monotonic()
        st = self.states.get(ref)
        source_id = ref.split(":", 1)[0]
        rt = self.sources.get(source_id)
        if st is None:
            if rt is not None and not rt.polled_once and rt.error is None:
                return "pending", None, "waiting for first reading"
            if rt is None:
                return "missing", None, f"sensor source '{source_id}' does not exist or is disabled"
            if rt.error:
                return "error", None, rt.error
            return "missing", None, "sensor not reported by its source"
        age = st.age(now)
        if age is None:
            if not (rt and rt.polled_once):
                return "pending", None, "waiting for first reading"
            return "error", None, st.message or "no valid reading yet"
        if age > self.stale_timeout:
            return "stale", st.value, f"no valid reading for {int(age)} s"
        if st.status == ERROR:
            # Last good value is still fresh; keep using it until it goes stale.
            return OK, st.value, st.message
        return st.status, st.value, st.message

    def all_pending(self) -> bool:
        return any(not rt.polled_once for rt in self.sources.values() if rt.plugin and not rt.plugin.push)

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        out = []
        for ref, st in sorted(self.states.items()):
            status, value, message = self.effective(ref, now)
            age = st.age(now)
            out.append({
                "ref": ref, "source": st.source_id, "id": st.sensor_id, "label": st.label,
                "value": value, "unit": st.unit, "status": status, "message": message,
                "age": round(age, 1) if age is not None else None,
            })
        return out

    def sources_snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [{
            "id": sid, "type": rt.cfg.type, "name": rt.cfg.name, "error": rt.error,
            "sensors": len(rt.sensors),
            "last_poll_age": round(now - rt.last_poll, 1) if rt.last_poll else None,
        } for sid, rt in self.sources.items()]
