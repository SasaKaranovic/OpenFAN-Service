import asyncio
import json
import time

import pytest

from openfan_service.models import SensorSource
from openfan_service.sensors import PluginRegistry
from openfan_service.sensors.base import Reading
from openfan_service.sensors.manager import SensorManager
from openfan_service.sensors.parsing import parse_text, to_float
from openfan_service.sensors.plugins import CommandSensor, FileSensor, HwmonSensor, PushSensor, UnraidDisks

DISKS_INI = '''["parity"]
name="parity"
temp="*"
status="DISK_OK"
["disk1"]
name="disk1"
id="WDC_WD80_ABC"
temp="32"
status="DISK_OK"
["disk2"]
temp="45.5"
["disk9"]
temp="*"
status="DISK_NP"
["disk3"]
temp="*"
status="DISK_NP_MISSING"
["cache"]
temp=""
'''


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_to_float():
    assert to_float("45") == 45
    assert to_float("45,5 °C") == 45.5
    assert to_float(" -3.25") == -3.25
    assert to_float("*") is None
    assert to_float("") is None
    assert to_float(float("nan")) is None
    assert to_float(True) is None


@pytest.mark.parametrize("policy,value,status", [("zero", 0.0, "sleeping"), ("ignore", None, "sleeping"), ("failsafe", None, "error")])
def test_unraid_spun_down_policy(tmp_path, policy, value, status):
    p = UnraidDisks("unraid", {"path": write(tmp_path, "disks.ini", DISKS_INI), "spun_down": policy})
    r = p.read()
    assert r["parity"].value == value and r["parity"].status == status


def test_unraid_parsing(tmp_path):
    r = UnraidDisks("unraid", {"path": write(tmp_path, "disks.ini", DISKS_INI)}).read()
    assert r["disk1"].value == 32 and r["disk1"].label == "WDC_WD80_ABC"
    assert r["disk2"].value == 45.5            # decimals no longer crash
    assert "disk9" not in r                    # empty slot skipped
    assert r["disk3"].status == "error"        # missing disk is a fault
    assert r["cache"].status == "error"        # empty value is a fault, not 0


def test_unraid_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        UnraidDisks("u", {"path": str(tmp_path / "nope.ini")}).read()


def test_unraid_bad_option():
    with pytest.raises(ValueError):
        UnraidDisks("u", {"spun_down": "banana"})


def test_file_formats(tmp_path):
    assert FileSensor("f", {"path": write(tmp_path, "a", "45123\n"), "scale": 0.001}).read()["value"].value == 45.12
    lines = FileSensor("f", {"path": write(tmp_path, "b", "cpu=50\ngpu: 61.5\n# x\nnvme 40"), "format": "lines"}).read()
    assert {k: v.value for k, v in lines.items()} == {"cpu": 50, "gpu": 61.5, "nvme": 40}
    js = FileSensor("f", {"path": write(tmp_path, "c", json.dumps({"a": {"t": [1, {"v": 33}]}, "b": 2})),
                          "format": "json", "fields": "deep = a.t.1.v\nb"}).read()
    assert js["deep"].value == 33 and js["b"].value == 2


def test_file_max_age(tmp_path):
    import os
    path = write(tmp_path, "old", "40")
    os.utime(path, (time.time() - 500, time.time() - 500))
    with pytest.raises(RuntimeError):
        FileSensor("f", {"path": path, "max_file_age": 60}).read()


def test_command(tmp_path):
    r = CommandSensor("c", {"command": "echo 'a=41'; echo 'b=42'", "format": "lines"}).read()
    assert r["a"].value == 41 and r["b"].value == 42
    with pytest.raises(RuntimeError):
        CommandSensor("c", {"command": "exit 3"}).read()


def test_hwmon(tmp_path):
    hw = tmp_path / "hwmon0"
    hw.mkdir()
    (hw / "name").write_text("k10temp\n")
    (hw / "temp1_input").write_text("52375\n")
    (hw / "temp1_label").write_text("Tctl\n")
    r = HwmonSensor("h", {"path": str(tmp_path)}).read()
    assert r["k10temp_0_tctl"].value == 52.4


def test_push_payloads():
    assert PushSensor.parse_payload({"cpu": 50, "x": "nope", "gpu": {"value": 60, "label": "RTX"}})["gpu"].label == "RTX"
    assert PushSensor.parse_payload([{"id": "a", "value": 30}])["a"].value == 30


def test_parse_text_unknown_format():
    with pytest.raises(ValueError):
        parse_text("1", "xml")


# -- manager -------------------------------------------------------------------
class FakePlugin:
    push = False

    def __init__(self, readings):
        self.readings = readings

    def read(self):
        if isinstance(self.readings, Exception):
            raise self.readings
        return self.readings


def make_manager(stale=60):
    m = SensorManager(PluginRegistry(), stale_timeout=stale)
    return m


def test_manager_staleness_and_errors():
    m = make_manager(stale=60)
    from openfan_service.sensors.manager import SourceRuntime
    rt = SourceRuntime(cfg=SensorSource(id="s", name="S", type="demo"))
    rt.plugin = FakePlugin({})
    m.sources["s"] = rt
    assert m.effective("s:a")[0] == "pending"
    m._update(rt, {"a": Reading(40.0)})
    assert m.effective("s:a") == ("ok", 40.0, None)
    # A failing read keeps the last good value until it goes stale...
    m._fail(rt, "boom")
    status, value, _ = m.effective("s:a")
    assert status == "ok" and value == 40.0
    # ...then the sensor is stale.
    future = time.monotonic() + 61
    assert m.effective("s:a", now=future)[0] == "stale"
    # A sensor that disappears from a successful read is an error
    m._update(rt, {"b": Reading(30.0)})
    assert m.states["s:a"].status == "error"
    # Unknown source
    assert m.effective("zz:a")[0] == "missing"


def test_manager_runs_and_stops_sources(tmp_path):
    async def run():
        m = make_manager()
        path = write(tmp_path, "t", "44")
        m.apply([SensorSource(id="f", name="F", type="file", poll_interval=0.5, options={"path": path})])
        await asyncio.sleep(0.3)
        assert m.effective("f:value")[1] == 44
        m.apply([])
        assert "f" not in m.sources and "f:value" not in m.states
        await m.stop()
    asyncio.run(run())


def test_manager_bad_plugin_type():
    async def run():
        m = make_manager()
        m.apply([SensorSource(id="x", name="X", type="nope")])
        assert m.sources["x"].error
        assert m.effective("x:a")[0] == "error"
    asyncio.run(run())


def test_user_plugin_loading(tmp_path):
    (tmp_path / "mine.py").write_text(
        "from openfan_service.sensors import SensorPlugin, Reading\n"
        "class Mine(SensorPlugin):\n    type_name='mine'\n    def read(self):\n        return {'x': Reading(1.0)}\n")
    (tmp_path / "broken.py").write_text("raise RuntimeError('nope')\n")
    reg = PluginRegistry()
    reg.load_user_plugins(tmp_path)
    assert reg.get("mine") is not None
    assert any("broken.py" in e for e in reg.load_errors)
