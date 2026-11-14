"""The control loop and the fail-safe logic.

Each cycle (default every 2 s) the engine decides a speed for every fan:

* **unmanaged** – the fan has no curve. The service does not touch it.
* **starting** – the service just started and sensors have not reported yet.
  Fans with a curve run at the fail-safe speed until the first reading.
* **ok** – the curve's sensors are fine; speed comes from the curve.
* **failsafe** – something is wrong (curve deleted, sensor missing, stale or
  failing, curve has no sensors). The fan runs at the fail-safe speed (100 %)
  until the problem is gone, then returns to its curve automatically.
* **override** – the user pressed "Set all to 100 %".
* **offline** – the fan's controller is not connected.

Entering and leaving fail-safe raises an event and, if configured, a
notification.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config_store import ConfigStore
from .controllers import ControllerManager
from .curves import apply_hysteresis, combine, evaluate, to_pwm
from .events import EventLog
from .history import History
from .models import Config, Curve
from .notifier import Notifier
from .sensors.manager import SensorManager

log = logging.getLogger("openfan.engine")

OK, FAILSAFE, UNMANAGED, STARTING, OVERRIDE, OFFLINE = "ok", "failsafe", "unmanaged", "starting", "override", "offline"


@dataclass
class Decision:
    state: str
    pwm: int | None
    temp: float | None = None          # combined input temperature
    eff_temp: float | None = None      # temperature after hysteresis
    sensor: str | None = None          # sensor that set the value (max/min)
    reasons: list[str] = field(default_factory=list)
    note: str | None = None


def decide(curve: Curve | None, curve_id: str | None, sensors: SensorManager, *, prev_eff: float | None,
           failsafe_pwm: int, starting: bool, now: float | None = None) -> Decision:
    """Decide what one fan should do. Pure apart from reading sensor state."""
    if curve_id is None:
        return Decision(UNMANAGED, None)
    if curve is None:
        return Decision(FAILSAFE, failsafe_pwm, reasons=[f"its curve '{curve_id}' no longer exists"])
    if not curve.sensors:
        return Decision(FAILSAFE, failsafe_pwm, reasons=[f"curve '{curve.name}' has no sensors"])

    values: list[tuple[float, str]] = []
    problems: list[str] = []
    pending = False
    sleeping_only = 0
    ignored = 0
    for ref in curve.sensors:
        status, value, message = sensors.effective(ref, now)
        if status == "ok" and value is not None:
            values.append((value, ref))
        elif status == "sleeping":
            if value is not None:
                values.append((value, ref))
            else:
                sleeping_only += 1
        elif status == "pending":
            pending = True
        elif status == "ignored":
            ignored += 1
        else:
            problems.append(f"{ref}: {message or status}")

    if pending and starting:
        return Decision(STARTING, failsafe_pwm, note="waiting for first sensor readings")
    if pending:
        problems.extend(f"{r}: no reading yet" for r in curve.sensors if sensors.effective(r, now)[0] == "pending")
    if problems:
        return Decision(FAILSAFE, failsafe_pwm, reasons=problems)

    if not values and not sleeping_only:
        # Every sensor of the curve is ignored: there is nothing to follow.
        return Decision(FAILSAFE, failsafe_pwm, reasons=[f"all sensors of curve '{curve.name}' are ignored"])
    if not values:
        # Every sensor is asleep and set to 'ignore': run at the curve's lowest point.
        pwm = to_pwm(evaluate(curve.points, float("-inf"), curve.type), curve.min_pwm)
        return Decision(OK, pwm, note="all sensors are spun down")

    temp = combine([v for v, _ in values], curve.combine)
    driver = None
    if curve.combine in ("max", "min"):
        driver = next(ref for v, ref in values if v == temp)
    eff = apply_hysteresis(prev_eff, temp, curve.hysteresis)
    pwm = to_pwm(evaluate(curve.points, eff, curve.type), curve.min_pwm)
    return Decision(OK, pwm, temp=round(temp, 2), eff_temp=round(eff, 2), sensor=driver)


@dataclass
class FanRuntime:
    key: str
    controller: str
    index: int
    name: str
    curve_id: str | None = None
    decision: Decision = field(default_factory=lambda: Decision(UNMANAGED, None))
    eff_temp: float | None = None
    failsafe_since: float | None = None
    applied: bool = False


class Engine:
    def __init__(self, store: ConfigStore, sensors: SensorManager, controllers: ControllerManager,
                 history: History, notifier: Notifier, events: EventLog) -> None:
        self.store = store
        self.sensors = sensors
        self.controllers = controllers
        self.history = history
        self.notifier = notifier
        self.events = events
        self.fans: dict[str, FanRuntime] = {}
        self.started = time.monotonic()
        self.last_cycle: float | None = None
        self.last_cycle_wall: float | None = None
        self.cycle_error: str | None = None
        self.cycles = 0
        self.override: dict[str, Any] | None = None
        self._next_history = 0.0
        self._ctrl_alerted: set[str] = set()
        self._task: asyncio.Task | None = None
        controllers.on_status = self._controller_status

    # -- configuration -------------------------------------------------------------
    def apply_config(self, cfg: Config) -> None:
        self.sensors.stale_timeout = cfg.settings.stale_timeout
        self.sensors.apply(cfg.sources)
        self.controllers.apply(cfg.controllers)
        self.notifier.configure(cfg.notifications)
        self.history.retention_days = cfg.settings.history_retention_days

    def on_config_change(self, old: Config, new: Config) -> None:
        self.apply_config(new)
        self.events.add("info", "config", "Configuration saved")

    # -- running -------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run(), name="engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        cfg = self.store.config
        final: dict[str, dict[int, int]] = {}
        for fan in self.fans.values():
            if fan.decision.state in (OK, FAILSAFE, STARTING, OVERRIDE):
                final.setdefault(fan.controller, {})[fan.index] = cfg.settings.exit_pwm
        if final:
            log.info("Stopping: setting managed fans to %d%%", cfg.settings.exit_pwm)
        await self.controllers.close(final)

    async def run(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                await self.cycle()
                self.cycle_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the loop die
                self.cycle_error = f"{exc.__class__.__name__}: {exc}"
                log.exception("Control cycle failed")
            self.last_cycle = time.monotonic()
            self.last_cycle_wall = time.time()
            self.cycles += 1
            interval = self.store.config.settings.update_interval
            await asyncio.sleep(max(0.2, interval - (time.monotonic() - t0)))

    def starting(self, cfg: Config) -> bool:
        return time.monotonic() - self.started < cfg.settings.stale_timeout and self.sensors.all_pending()

    async def cycle(self) -> None:
        cfg = self.store.config
        now = time.monotonic()
        await self.controllers.ensure_connected()
        starting = self.starting(cfg)
        fs_pwm = cfg.settings.failsafe_pwm

        if self.override and self.override["until"] < time.time():
            self.override = None
            self.events.add("info", "override", "Manual override ended, fans back on their curves")

        seen: set[str] = set()
        entered, recovered = [], []
        for cid, rt in self.controllers.ctrls.items():
            count = rt.info.fan_count if rt.info else 0
            indexes = set(range(count)) | {f.index for f in cfg.fans if f.controller == cid}
            for idx in sorted(indexes):
                key = f"{cid}:{idx}"
                seen.add(key)
                fan_cfg = cfg.fan(cid, idx)
                fan = self.fans.get(key)
                if fan is None:
                    fan = self.fans[key] = FanRuntime(key=key, controller=cid, index=idx, name="")
                fan.name = (fan_cfg.name if fan_cfg and fan_cfg.name else f"Fan {idx}")
                curve_id = fan_cfg.curve if fan_cfg else None
                if curve_id != fan.curve_id:
                    fan.eff_temp = None  # new curve: forget hysteresis memory
                fan.curve_id = curve_id

                d = decide(cfg.curve(curve_id), curve_id, self.sensors, prev_eff=fan.eff_temp,
                           failsafe_pwm=fs_pwm, starting=starting, now=now)
                if d.state == OK:
                    fan.eff_temp = d.eff_temp
                # Fail-safe bookkeeping uses the curve decision itself, before any
                # override/offline substitution, so every alert gets its recovery.
                if d.state == FAILSAFE:
                    if fan.failsafe_since is None:
                        fan.failsafe_since = time.time()
                        entered.append((fan, d))
                elif d.state in (OK, UNMANAGED) and fan.failsafe_since is not None:
                    recovered.append((fan, time.time() - fan.failsafe_since))
                    fan.failsafe_since = None
                if self.override:
                    d = Decision(OVERRIDE, self.override["pwm"], temp=d.temp, sensor=d.sensor,
                                 reasons=d.reasons, note="manual override")
                if not rt.connected or idx >= count:
                    d = Decision(OFFLINE, None, temp=d.temp, reasons=d.reasons,
                                 note=rt.error or "controller not connected")
                fan.decision = d

                if d.pwm is not None:
                    fan.applied = await self.controllers.set_pwm(cid, idx, d.pwm, cfg.settings.keepalive_interval)
            await self.controllers.read_rpm(cid)

        # Configured fans whose controller is gone or disabled.
        for f in cfg.fans:
            if f.key not in seen:
                fan = self.fans.setdefault(f.key, FanRuntime(key=f.key, controller=f.controller, index=f.index, name=""))
                fan.name, fan.curve_id = f.name or f"Fan {f.index}", f.curve
                fan.decision = Decision(OFFLINE, None, note=f"controller '{f.controller}' is not configured or disabled")
                seen.add(f.key)
        for key in [k for k in self.fans if k not in seen]:
            del self.fans[key]

        self._report(entered, recovered)
        self._check_controllers(cfg)
        if now >= self._next_history:
            self._next_history = now + cfg.settings.history_interval
            await self._sample()

    # -- reporting -----------------------------------------------------------------
    def _report(self, entered, recovered) -> None:
        if entered:
            lines = [f"• {f.name}: {'; '.join(d.reasons)}" for f, d in entered]
            msg = f"{len(entered)} fan(s) set to {self.store.config.settings.failsafe_pwm}%:\n" + "\n".join(lines)
            self.events.add("error", "failsafe", "Fail-safe ON. " + msg.replace("\n", " "))
            self.notifier.notify("failsafe", "OpenFAN: fail-safe active", msg)
        if recovered:
            names = ", ".join(f"{f.name} (after {int(s)} s)" for f, s in recovered)
            self.events.add("info", "recovery", f"Recovered: {names} back on their curves")
            self.notifier.notify("recovery", "OpenFAN: recovered", f"Back to normal: {names}")

    def _controller_status(self, cid: str, connected: bool, message: str) -> None:
        if connected:
            if cid in self._ctrl_alerted:
                self._ctrl_alerted.discard(cid)
                self.events.add("info", "controller", f"Controller '{cid}' reconnected: {message}")
                self.notifier.notify("controller", "OpenFAN: controller reconnected", f"'{cid}': {message}")
            else:
                self.events.add("info", "controller", f"Controller '{cid}' connected: {message}")
        else:
            self.events.add("warning", "controller", f"Controller '{cid}' disconnected: {message}")

    def _check_controllers(self, cfg: Config) -> None:
        """Alert once if a controller stays unreachable longer than the stale timeout."""
        now = time.monotonic()
        for cid, rt in self.controllers.ctrls.items():
            if rt.connected or cid in self._ctrl_alerted or rt.disconnected_since is None:
                continue
            if now - rt.disconnected_since >= cfg.settings.stale_timeout:
                self._ctrl_alerted.add(cid)
                msg = (f"Controller '{rt.cfg.name}' ({rt.target}) has been unreachable for "
                       f"{int(now - rt.disconnected_since)} s: {rt.error}. Its fans cannot be controlled.")
                self.events.add("error", "controller", msg)
                self.notifier.notify("controller", "OpenFAN: controller unreachable", msg)

    async def _sample(self) -> None:
        ts = int(time.time())
        rows: list[tuple[int, str, float | None]] = []
        for fan in self.fans.values():
            d = fan.decision
            if d.state in (UNMANAGED, OFFLINE):
                continue
            rpm = self.controllers.ctrls[fan.controller].rpm.get(fan.index) if fan.controller in self.controllers.ctrls else None
            rows += [(ts, f"fan:{fan.key}:pwm", d.pwm), (ts, f"fan:{fan.key}:rpm", rpm),
                     (ts, f"fan:{fan.key}:temp", d.temp), (ts, f"fan:{fan.key}:fs", 1.0 if d.state == FAILSAFE else 0.0)]
        for s in self.sensors.snapshot():
            if s["status"] in ("ok", "sleeping") and s["value"] is not None:
                rows.append((ts, f"sensor:{s['ref']}", s["value"]))
        try:
            await self.history.write(rows)
        except Exception:
            log.debug("history write failed", exc_info=True)

    # -- state for the UI ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        cfg = self.store.config
        fans = []
        for fan in sorted(self.fans.values(), key=lambda f: (f.controller, f.index)):
            d = fan.decision
            rt = self.controllers.ctrls.get(fan.controller)
            curve = cfg.curve(fan.curve_id)
            fans.append({
                "key": fan.key, "controller": fan.controller, "index": fan.index, "name": fan.name,
                "curve": fan.curve_id, "curve_name": curve.name if curve else None,
                "state": d.state, "pwm": d.pwm, "rpm": rt.rpm.get(fan.index) if rt else None,
                "temp": d.temp, "sensor": d.sensor, "sensors": curve.sensors if curve else [],
                "combine": curve.combine if curve else None,
                "reasons": d.reasons, "note": d.note, "failsafe_since": fan.failsafe_since,
            })
        now = time.monotonic()
        return {
            "fans": fans,
            "engine": {
                "running": self.last_cycle is not None and now - self.last_cycle < max(10, cfg.settings.update_interval * 5),
                "last_cycle_age": round(now - self.last_cycle, 1) if self.last_cycle else None,
                "interval": cfg.settings.update_interval,
                "starting": self.starting(cfg),
                "error": self.cycle_error,
                "override": self.override,
                "uptime": round(now - self.started),
            },
        }

    def set_override(self, pwm: int, minutes: float) -> None:
        self.override = {"pwm": int(pwm), "until": time.time() + minutes * 60}
        self.events.add("warning", "override", f"Manual override: all fans {pwm}% for {minutes:g} min")

    def clear_override(self) -> None:
        if self.override:
            self.override = None
            self.events.add("info", "override", "Manual override cancelled")
