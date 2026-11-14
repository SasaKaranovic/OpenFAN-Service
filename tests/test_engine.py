import asyncio
import time

import pytest

from openfan_service.config_store import ConfigStore
from openfan_service.controllers import ControllerManager
from openfan_service.engine import Engine
from openfan_service.events import EventLog
from openfan_service.history import History
from openfan_service.models import Config, Controller, Curve, Fan, SensorSource
from openfan_service.notifier import Notifier
from openfan_service.sensors import PluginRegistry
from openfan_service.sensors.base import Reading
from openfan_service.sensors.manager import SensorManager

CURVE = {"id": "c", "name": "Disks", "sensors": ["p:a", "p:b"], "points": [[30, 30], [50, 100]], "hysteresis": 0}


class Harness:
    def __init__(self, tmp_path, *, curve=CURVE, fans=((0, "c"), (1, None)), controller_type="demo", stale=60):
        cfg = Config(
            controllers=[Controller(id="main", type=controller_type, options={"fan_count": 3, "url": "http://127.0.0.1:9"})],
            sources=[SensorSource(id="p", name="push", type="push")],
            curves=[Curve(**curve)] if curve else [],
            fans=[Fan(controller="main", index=i, name=f"F{i}", curve=c) for i, c in fans],
        )
        cfg.settings.stale_timeout = stale
        self.store = ConfigStore(tmp_path)
        self.store.replace(cfg)
        self.sensors = SensorManager(PluginRegistry(), stale)
        self.ctrls = ControllerManager()
        self.history = History(tmp_path / "h.sqlite")
        self.notifier = Notifier()
        self.sent = []
        self.notifier.notify = lambda kind, title, body: self.sent.append((kind, title, body))
        self.events = EventLog()
        self.engine = Engine(self.store, self.sensors, self.ctrls, self.history, self.notifier, self.events)
        self.store.on_change(self.engine.on_config_change)

    async def start(self):
        await self.history.open()
        self.engine.apply_config(self.store.config)

    def push(self, **values):
        self.sensors.push("p", {k: Reading(v) for k, v in values.items()})

    def driver_pwm(self):
        return dict(self.ctrls.ctrls["main"].driver._pwm)

    def fan(self, idx):
        return self.engine.fans[f"main:{idx}"].decision

    def kinds(self):
        return [k for k, _, _ in self.sent]


def run(coro):
    return asyncio.run(coro)


def test_normal_operation(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=40, b=35)
        await h.engine.cycle()
        d = h.fan(0)
        assert d.state == "ok" and d.temp == 40 and d.sensor == "p:a"
        assert d.pwm == 65                      # 30 + (100-30) * (40-30)/20
        assert h.driver_pwm() == {0: 65}        # fan 1 is unmanaged: never touched
        assert h.fan(1).state == "unmanaged"
        assert h.fan(2).state == "unmanaged"
        assert h.sent == []
    run(go())


def test_startup_runs_fans_at_failsafe_without_alerts(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        # Push source never polled -> sensor pending; push sources do not block startup,
        # so emulate a polled source that has not answered yet.
        from openfan_service.sensors.manager import SourceRuntime

        class Slow:
            push = False

            def close(self):
                pass
        h.store.mutate(lambda d: d["curves"][0].update(sensors=["slow:x"]))
        rt = SourceRuntime(cfg=SensorSource(id="slow", name="s", type="demo"))
        rt.plugin = Slow()
        h.sensors.sources["slow"] = rt
        await h.engine.cycle()
        assert h.fan(0).state == "starting" and h.fan(0).pwm == 100
        assert h.driver_pwm()[0] == 100
        assert "failsafe" not in h.kinds()
    run(go())


def test_deleted_curve_goes_failsafe_and_recovers(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=35, b=35)
        await h.engine.cycle()
        assert h.fan(0).state == "ok"
        h.store.mutate(lambda d: d.update(curves=[]))
        await h.engine.cycle()
        assert h.fan(0).state == "failsafe" and h.fan(0).pwm == 100
        assert h.driver_pwm()[0] == 100
        assert h.kinds() == ["failsafe"]
        assert "no longer exists" in h.sent[0][2]
        # A second cycle must not notify again
        await h.engine.cycle()
        assert h.kinds() == ["failsafe"]
        # Give the fan a curve again -> recovery
        h.store.mutate(lambda d: d["curves"].append(dict(CURVE)))
        await h.engine.cycle()
        assert h.fan(0).state == "ok"
        assert h.kinds() == ["failsafe", "recovery"]
    run(go())


def test_stale_sensor_goes_failsafe(tmp_path):
    async def go():
        h = Harness(tmp_path, stale=60)
        await h.start()
        h.push(a=35, b=35)
        await h.engine.cycle()
        h.sensors.states["p:b"].last_ok = time.monotonic() - 61
        await h.engine.cycle()
        d = h.fan(0)
        assert d.state == "failsafe" and d.pwm == 100
        assert any("p:b" in r for r in d.reasons)
        h.push(b=36)
        await h.engine.cycle()
        assert h.fan(0).state == "ok"
        assert h.kinds() == ["failsafe", "recovery"]
    run(go())


def test_missing_sensor_is_failsafe_not_minimum(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=35)          # b never reported
        await h.engine.cycle()
        assert h.fan(0).state == "failsafe" and h.fan(0).pwm == 100
    run(go())


def test_curve_without_sensors_is_failsafe(tmp_path):
    async def go():
        h = Harness(tmp_path, curve={**CURVE, "sensors": []})
        await h.start()
        await h.engine.cycle()
        assert h.fan(0).state == "failsafe"
    run(go())


def test_sleeping_ignored_sensors(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.sensors.push("p", {"a": Reading(None, "sleeping"), "b": Reading(None, "sleeping")})
        await h.engine.cycle()
        d = h.fan(0)
        assert d.state == "ok" and d.pwm == 30 and "spun down" in d.note
        h.sensors.push("p", {"a": Reading(None, "sleeping"), "b": Reading(44.0)})
        await h.engine.cycle()
        assert h.fan(0).temp == 44
    run(go())


def test_hysteresis_in_engine(tmp_path):
    async def go():
        h = Harness(tmp_path, curve={**CURVE, "hysteresis": 3})
        await h.start()
        h.push(a=40, b=0)
        await h.engine.cycle()
        assert h.fan(0).pwm == 65
        h.push(a=38)
        await h.engine.cycle()
        assert h.fan(0).pwm == 65   # held
        h.push(a=37)
        await h.engine.cycle()
        assert h.fan(0).pwm == 54   # dropped 3 °C: follows (30 + 70*7/20 = 54.5 -> 54)
    run(go())


def test_override(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=35, b=35)
        h.engine.set_override(100, 5)
        await h.engine.cycle()
        assert h.fan(0).state == "override" and h.driver_pwm() == {0: 100, 1: 100, 2: 100}
        h.engine.override["until"] = time.time() - 1
        await h.engine.cycle()
        assert h.fan(0).state == "ok"
    run(go())


def test_unreachable_controller_alerts_once(tmp_path):
    async def go():
        h = Harness(tmp_path, controller_type="openfan_rest", stale=5)
        await h.start()
        h.push(a=35, b=35)
        await h.engine.cycle()
        assert h.fan(0).state == "offline"
        rt = h.ctrls.ctrls["main"]
        assert not rt.connected and rt.error
        rt.disconnected_since = time.monotonic() - 10
        rt.next_retry = time.monotonic() + 999
        await h.engine.cycle()
        await h.engine.cycle()
        assert h.kinds() == ["controller"]
    run(go())


def test_stop_sets_exit_speed(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=31, b=30)
        await h.engine.cycle()
        drv = h.ctrls.ctrls["main"].driver
        assert drv._pwm == {0: 34}
        await h.engine.stop()
        assert drv._pwm == {0: 100}    # unmanaged fans untouched
    run(go())


def test_history_is_recorded(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=40, b=30)
        await h.engine.cycle()
        data = await h.history.query(["fan:main:0:pwm", "fan:main:0:temp", "sensor:p:a"], time.time() - 60, time.time() + 1)
        assert data["fan:main:0:pwm"][0][1] == 65
        assert data["fan:main:0:temp"][0][1] == 40
        assert data["sensor:p:a"][0][1] == 40
        await h.history.close()
    run(go())


def test_cycle_survives_exceptions(tmp_path, monkeypatch):
    async def go():
        h = Harness(tmp_path)
        await h.start()

        async def boom():
            raise RuntimeError("kaput")
        monkeypatch.setattr(h.engine, "cycle", boom)
        task = asyncio.create_task(h.engine.run())
        await asyncio.sleep(0.05)
        assert h.engine.cycle_error and "kaput" in h.engine.cycle_error
        assert h.engine.last_cycle is not None
        task.cancel()
    run(go())


def test_ignored_sensor_is_left_out_of_the_curve(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        h.push(a=40, b=35)
        h.store.mutate(lambda d: d["sources"][0].update(ignored=["a"]))
        assert h.sensors.effective("p:a")[0] == "ignored"
        await h.engine.cycle()
        assert h.fan(0).state == "ok" and h.fan(0).temp == 35      # only b counts
        # An ignored sensor that fails is not a problem
        h.sensors.states["p:a"].last_ok = time.monotonic() - 999
        await h.engine.cycle()
        assert h.fan(0).state == "ok" and h.kinds() == []
        # All sensors ignored -> nothing to follow -> fail-safe
        h.store.mutate(lambda d: d["sources"][0].update(ignored=["a", "b"]))
        await h.engine.cycle()
        assert h.fan(0).state == "failsafe" and "ignored" in h.fan(0).reasons[0]
    run(go())


def test_ignoring_does_not_restart_the_source(tmp_path):
    async def go():
        h = Harness(tmp_path)
        await h.start()
        rt = h.sensors.sources["p"]
        h.store.mutate(lambda d: d["sources"][0].update(ignored=["a"]))
        assert h.sensors.sources["p"] is rt and rt.cfg.ignored == ["a"]
    run(go())
