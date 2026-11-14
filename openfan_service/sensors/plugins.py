"""Built-in sensor plugins."""

from __future__ import annotations

import json
import math
import random
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from .base import ERROR, OK, SLEEPING, Option, Reading, SensorPlugin
from .parsing import parse_json, parse_text, safe_id, to_float

FORMAT_CHOICES = [
    ("number", "Single number"),
    ("lines", "One 'name=value' per line"),
    ("json", "JSON"),
]


def _check_age(path: Path, max_age: float) -> None:
    if max_age and max_age > 0:
        age = time.time() - path.stat().st_mtime
        if age > max_age:
            raise RuntimeError(f"{path} has not been updated for {int(age)} s")


# ---------------------------------------------------------------------------
class UnraidDisks(SensorPlugin):
    type_name = "unraid_disks"
    title = "Unraid disks"
    description = ("Disk temperatures from Unraid's disks.ini. Mount /var/local/emhttp from the host "
                   "(read-only) and point the path at disks.ini.")
    default_poll_interval = 5.0
    options = [
        Option("path", "Path to disks.ini", default="/unraid/disks.ini", required=True),
        Option("spun_down", "Spun-down disks ('*')", type="select", default="zero",
               choices=[("zero", "Report 0 °C"), ("ignore", "Ignore (leave out of the curve)"),
                        ("failsafe", "Treat as a fault (fail-safe)")],
               help="Unraid reports '*' instead of a temperature while a disk is spun down."),
        Option("max_file_age", "Max file age (s)", type="number", default=0,
               help="Fault if disks.ini has not changed for this long. 0 = off."),
    ]

    SECTION = re.compile(r'^\s*\[\s*"?([^"\]]+)"?\s*\]\s*$')
    KEYVAL = re.compile(r'^\s*([A-Za-z0-9_.]+)\s*=\s*"?(.*?)"?\s*$')

    @classmethod
    def parse(cls, text: str) -> dict[str, dict[str, str]]:
        sections: dict[str, dict[str, str]] = {}
        current = None
        for line in text.splitlines():
            m = cls.SECTION.match(line)
            if m:
                current = sections.setdefault(m.group(1).strip(), {})
                continue
            m = cls.KEYVAL.match(line)
            if m and current is not None:
                current[m.group(1)] = m.group(2)
        return sections

    def read(self) -> dict[str, Reading]:
        path = Path(self.opts["path"])
        _check_age(path, self.opts["max_file_age"] or 0)
        sections = self.parse(path.read_text(encoding="utf-8", errors="replace"))
        if not sections:
            raise RuntimeError(f"{path} contains no disks")
        out: dict[str, Reading] = {}
        for name, data in sections.items():
            status = data.get("status", "")
            if status == "DISK_NP":
                continue  # empty slot
            label = data.get("id") or None
            if "MISSING" in status or status == "DISK_NP_DSBL":
                out[safe_id(name)] = Reading(None, ERROR, label=label, message=f"disk {status}")
                continue
            raw = data.get("temp", "").strip()
            if raw == "*":
                policy = self.opts["spun_down"]
                if policy == "zero":
                    out[safe_id(name)] = Reading(0.0, SLEEPING, label=label, message="spun down")
                elif policy == "ignore":
                    out[safe_id(name)] = Reading(None, SLEEPING, label=label, message="spun down (ignored)")
                else:
                    out[safe_id(name)] = Reading(None, ERROR, label=label, message="spun down (treated as fault)")
                continue
            val = to_float(raw)
            if val is None:
                out[safe_id(name)] = Reading(None, ERROR, label=label, message=f"no temperature ({raw!r})")
            else:
                out[safe_id(name)] = Reading(val, OK, label=label)
        return out


# ---------------------------------------------------------------------------
class FileSensor(SensorPlugin):
    type_name = "file"
    title = "File"
    description = "Read values from a file that another program writes (number, name=value lines or JSON)."
    options = [
        Option("path", "File path (inside the container)", required=True, default=""),
        Option("format", "Format", type="select", default="number", choices=FORMAT_CHOICES),
        Option("sensor_id", "Sensor name (single number)", default="value"),
        Option("fields", "JSON fields", type="textarea", default="",
               help="Optional. One 'name = path.to.value' per line. Empty = every top-level number."),
        Option("scale", "Multiply by", type="number", default=1,
               help="Use 0.001 for millidegrees (e.g. /sys files)."),
        Option("max_file_age", "Max file age (s)", type="number", default=0,
               help="Fault if the file has not changed for this long. 0 = off."),
    ]

    def read(self) -> dict[str, Reading]:
        path = Path(self.opts["path"])
        _check_age(path, self.opts["max_file_age"] or 0)
        text = path.read_text(encoding="utf-8", errors="replace")
        return parse_text(text, self.opts["format"], sensor_id=self.opts["sensor_id"],
                          scale=self.opts["scale"], fields=self.opts["fields"])


# ---------------------------------------------------------------------------
class CommandSensor(SensorPlugin):
    type_name = "command"
    title = "Command / script"
    description = ("Run a command and read its output (number, name=value lines or JSON). "
                   "Handy for nvidia-smi, smartctl, ipmitool or your own script.")
    default_poll_interval = 10.0
    options = [
        Option("command", "Command", required=True, default="",
               help="Runs in the container with /bin/sh. Example: nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader"),
        Option("format", "Output format", type="select", default="number", choices=FORMAT_CHOICES),
        Option("sensor_id", "Sensor name (single number)", default="value"),
        Option("fields", "JSON fields", type="textarea", default=""),
        Option("scale", "Multiply by", type="number", default=1),
        Option("timeout", "Timeout (s)", type="number", default=10),
    ]

    def read(self) -> dict[str, Reading]:
        proc = subprocess.run(  # noqa: S602 - the user configures their own command
            self.opts["command"], shell=True, capture_output=True, text=True,
            timeout=max(1.0, self.opts["timeout"] or 10),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RuntimeError(f"exit code {proc.returncode}: {err[-1][:120] if err else 'no output'}")
        return parse_text(proc.stdout, self.opts["format"], sensor_id=self.opts["sensor_id"],
                          scale=self.opts["scale"], fields=self.opts["fields"])


# ---------------------------------------------------------------------------
class HwmonSensor(SensorPlugin):
    type_name = "hwmon"
    title = "Linux hwmon (CPU, board, NVMe)"
    description = ("Temperatures the Linux kernel exposes in /sys/class/hwmon: CPU (k10temp, coretemp), "
                   "NVMe drives, motherboard chips. Works on bare-metal Linux, TrueNAS SCALE, Unraid and Proxmox.")
    default_poll_interval = 2.0
    options = [
        Option("path", "hwmon folder", default="/sys/class/hwmon"),
        Option("include", "Only chips matching", default="",
               help="Optional regular expression on the chip name, e.g. k10temp|coretemp|nvme"),
    ]

    def read(self) -> dict[str, Reading]:
        root = Path(self.opts["path"])
        if not root.is_dir():
            raise RuntimeError(f"{root} not found")
        include = re.compile(self.opts["include"]) if self.opts["include"] else None
        out: dict[str, Reading] = {}
        for hw in sorted(root.iterdir()):
            try:
                chip = (hw / "name").read_text().strip()
            except OSError:
                continue
            if include and not include.search(chip):
                continue
            for inp in sorted(hw.glob("temp*_input")):
                n = inp.name[: -len("_input")]
                try:
                    label = (hw / f"{n}_label").read_text().strip()
                except OSError:
                    label = n
                sid = safe_id(f"{chip}_{hw.name.removeprefix('hwmon')}_{label}".lower())
                try:
                    milli = int(inp.read_text().strip())
                except (OSError, ValueError):
                    out[sid] = Reading(None, ERROR, label=f"{chip} {label}", message="read failed")
                    continue
                out[sid] = Reading(round(milli / 1000.0, 1), OK, label=f"{chip} {label}")
        if not out:
            raise RuntimeError("no temperature sensors found" + (" matching the filter" if include else ""))
        return out


# ---------------------------------------------------------------------------
class HttpJsonSensor(SensorPlugin):
    type_name = "http_json"
    title = "HTTP / JSON"
    description = "Fetch a URL that returns JSON (Home Assistant, Glances, Netdata, your own endpoint...)."
    default_poll_interval = 10.0
    options = [
        Option("url", "URL", required=True, default=""),
        Option("fields", "Fields", type="textarea", default="",
               help="One 'name = path.to.value' per line, e.g. cpu = sensors.0.value. Empty = every top-level number."),
        Option("headers", "Extra headers", type="textarea", default="",
               help="One 'Header: value' per line, e.g. Authorization: Bearer abc"),
        Option("scale", "Multiply by", type="number", default=1),
        Option("timeout", "Timeout (s)", type="number", default=5),
    ]

    def read(self) -> dict[str, Reading]:
        req = urllib.request.Request(self.opts["url"], headers={"Accept": "application/json"})
        for line in (self.opts["headers"] or "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                req.add_header(k.strip(), v.strip())
        with urllib.request.urlopen(req, timeout=self.opts["timeout"] or 5) as resp:  # noqa: S310
            body = resp.read(2_000_000)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"response is not JSON: {exc.msg}") from exc
        return parse_json(data, scale=self.opts["scale"], fields=self.opts["fields"])


# ---------------------------------------------------------------------------
class PushSensor(SensorPlugin):
    type_name = "push"
    title = "Push (API)"
    description = ("Your own script sends values to the service: POST JSON like {\"cpu\": 52.5} to "
                   "/api/push/<source id>. Values go stale if they stop arriving.")
    push = True
    options = [
        Option("token", "Token", type="password", default="",
               help="Optional. If set, requests must send the header 'X-OpenFAN-Token: <token>'."),
    ]

    def read(self) -> dict[str, Reading]:  # pragma: no cover - never polled
        return {}

    @staticmethod
    def parse_payload(payload) -> dict[str, Reading]:
        if isinstance(payload, list):
            out = {}
            for item in payload:
                if isinstance(item, dict) and "id" in item:
                    out.update(parse_json({item["id"]: item.get("value")}))
            return out
        return parse_json(payload)


# ---------------------------------------------------------------------------
class DemoSensor(SensorPlugin):
    type_name = "demo"
    title = "Demo (simulated)"
    description = "Simulated temperatures for trying the service without hardware."
    default_poll_interval = 2.0
    options = [
        Option("count", "Number of sensors", type="number", default=4),
    ]

    def __init__(self, source_id, options):
        super().__init__(source_id, options)
        self._start = time.monotonic()
        self._rng = random.Random(source_id)

    def read(self) -> dict[str, Reading]:
        t = time.monotonic() - self._start
        out = {}
        for i in range(int(self.opts["count"] or 4)):
            base = 32 + i * 6
            val = base + 6 * math.sin(t / (90 + i * 25)) + self._rng.uniform(-0.4, 0.4)
            out[f"disk{i + 1}"] = Reading(round(val, 1), OK, label=f"Demo disk {i + 1}")
        return out


BUILTIN = [UnraidDisks, HwmonSensor, CommandSensor, FileSensor, HttpJsonSensor, PushSensor, DemoSensor]

