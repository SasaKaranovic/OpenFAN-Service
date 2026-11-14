"""Built-in controller drivers: OpenFAN over USB serial, OpenFAN over REST, and a demo."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from ..sensors.base import Option
from .base import ControllerDriver, ControllerError, ControllerInfo

log = logging.getLogger("openfan.controller")

# USB IDs of OpenFAN boards. The Micro's ID is assumed (Espressif default CDC).
KNOWN_USB_IDS: list[tuple[int, int, str]] = [
    (0x2E8A, 0x000A, "OpenFAN Controller"),
    (0x303A, 0x1001, "OpenFAN Micro"),
]


# ---------------------------------------------------------------------------
# Serial protocol (independent of pyserial so it can be unit-tested)
# ---------------------------------------------------------------------------
class Transport(Protocol):
    def write(self, data: bytes) -> int | None: ...
    def readline(self) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


def board_from_hw_info(hw: str) -> tuple[str, int | None]:
    """Map the firmware's hardware string to (board name, fan count)."""
    if "MCU:PICO2040" in hw:
        return "OpenFAN Controller", 10
    if "OpenFAN_Micro" in hw:
        return "OpenFAN Micro", 1
    if "MCU:PICO235" in hw:
        return "OpenFAN XL", 10
    return "OpenFAN (unknown board)", None


class OpenFanProtocol:
    """The OpenFAN text protocol: '>CCDATA\\r\\n' out, a line starting with '<' back."""

    CMD_RPM_ALL = 0x00
    CMD_SET_PWM = 0x02
    CMD_HW_INFO = 0x05
    CMD_FW_INFO = 0x06

    def __init__(self, transport: Transport, timeout: float = 1.5) -> None:
        self.t = transport
        self.timeout = timeout

    def command(self, cmd: int, data: bytes = b"", *, all_lines: bool = False) -> list[str]:
        payload = f">{cmd:02X}{data.hex().upper()}\r\n".encode()
        try:
            self.t.reset_input_buffer()
            self.t.write(payload)
        except Exception as exc:
            raise ControllerError(f"write failed: {exc}") from exc
        deadline = time.monotonic() + self.timeout
        lines: list[str] = []
        got = False
        while time.monotonic() < deadline:
            try:
                raw = self.t.readline()
            except Exception as exc:
                raise ControllerError(f"read failed: {exc}") from exc
            if not raw:
                if got:
                    break  # read timeout after the response: done
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("<"):
                got = True
                lines.append(line)
                if not all_lines:
                    break
            elif got:
                lines.append(line)
        if not got:
            raise ControllerError(f"no response to command {cmd:02X}")
        return lines

    def hw_info(self) -> str:
        return " ".join(self.command(self.CMD_HW_INFO, all_lines=True))

    def fw_info(self) -> str:
        return " ".join(self.command(self.CMD_FW_INFO, all_lines=True))

    def set_pwm(self, index: int, pwm: int) -> None:
        pwm = max(0, min(100, int(pwm)))
        self.command(self.CMD_SET_PWM, bytes([index & 0xFF, int(pwm * 255 / 100)]))

    def read_rpm(self) -> dict[int, int]:
        line = self.command(self.CMD_RPM_ALL)[0]
        return parse_rpm_line(line)


def parse_rpm_line(line: str) -> dict[int, int]:
    if "|" not in line:
        raise ControllerError(f"unexpected RPM response: {line[:60]!r}")
    out = {}
    for item in line.split("|", 1)[1].strip().rstrip(";").split(";"):
        if ":" not in item:
            continue
        idx, val = item.split(":", 1)
        try:
            out[int(idx, 16)] = int(val, 16)
        except ValueError:
            continue
    return out


class OpenFanSerial(ControllerDriver):
    type_name = "openfan_serial"
    title = "OpenFAN over USB (serial)"
    description = ("Talks to the controller directly over USB. Pass the device into the container, "
                   "e.g. devices: [/dev/ttyACM0]. Only one program can use the port at a time.")
    options = [
        Option("port", "Serial port", default="auto",
               help="'auto' finds the first OpenFAN board, or give a path such as /dev/ttyACM0 or /dev/serial/by-id/..."),
        Option("fan_count", "Number of fans", type="number", default=0,
               help="0 = detect from the board."),
    ]

    def __init__(self, controller_id, options):
        super().__init__(controller_id, options)
        self._serial = None
        self._proto: OpenFanProtocol | None = None
        self._port_name = self.opts["port"]

    @property
    def target(self) -> str:
        return self._port_name

    @staticmethod
    def list_ports() -> list[dict[str, Any]]:
        try:
            from serial.tools.list_ports import comports
        except ImportError:
            return []
        out = []
        for p in comports():
            known = next((name for vid, pid, name in KNOWN_USB_IDS if p.vid == vid and p.pid == pid), None)
            out.append({"device": p.device, "description": p.description, "vid": p.vid, "pid": p.pid,
                        "serial_number": p.serial_number, "openfan": known})
        return out

    def _find_port(self) -> str:
        if self._port_name and self._port_name != "auto":
            return self._port_name
        for p in self.list_ports():
            if p["openfan"]:
                return p["device"]
        raise ControllerError("no OpenFAN board found on USB. Is the device passed into the container?")

    def connect(self) -> ControllerInfo:
        self.close()
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover - present in the image
            raise ControllerError("pyserial is not installed") from exc
        port = self._find_port()
        try:
            self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.2, write_timeout=1.0)
        except Exception as exc:
            raise ControllerError(f"cannot open {port}: {exc}") from exc
        self._port_name = port
        self._proto = OpenFanProtocol(self._serial)
        hw = self._proto.hw_info()
        try:
            fw = self._proto.fw_info()
        except ControllerError:
            fw = ""
        name, count = board_from_hw_info(hw)
        fan_count = int(self.opts["fan_count"] or 0) or count or 10
        self._proto.read_rpm()  # proves the link works both ways
        return ControllerInfo(name=name, fan_count=fan_count, hardware=hw, firmware=fw)

    def set_pwm(self, index: int, pwm: int) -> None:
        if not self._proto:
            raise ControllerError("not connected")
        self._proto.set_pwm(index, pwm)

    def read_rpm(self) -> dict[int, int]:
        if not self._proto:
            raise ControllerError("not connected")
        return self._proto.read_rpm()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._proto = None


# ---------------------------------------------------------------------------
class OpenFanRest(ControllerDriver):
    type_name = "openfan_rest"
    title = "OpenFAN over network (REST API)"
    description = "Talks to the OpenFAN Controller app's REST API (the OpenFanController container, port 3000)."
    options = [
        Option("url", "Controller URL", required=True, default="http://192.168.1.10:3000",
               help="Address of the OpenFAN Controller web app, e.g. http://192.168.1.10:3000"),
        Option("fan_count", "Number of fans", type="number", default=0, help="0 = detect."),
        Option("timeout", "Timeout (s)", type="number", default=3),
    ]
    supports_fan_names = True

    @property
    def target(self) -> str:
        return self.opts["url"]

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.opts["url"].rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=float(self.opts["timeout"] or 3)) as resp:  # noqa: S310
                body = resp.read(1_000_000)
        except urllib.error.HTTPError as exc:
            raise ControllerError(f"HTTP {exc.code} from {path}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ControllerError(f"cannot reach {self.opts['url']}: {reason}") from exc
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ControllerError(f"invalid JSON from {path}") from exc
        if not isinstance(data, dict) or data.get("status") != "ok":
            msg = data.get("message") if isinstance(data, dict) else None
            raise ControllerError(f"controller refused {path}: {msg or data}")
        return data.get("data")

    def connect(self) -> ControllerInfo:
        info = self._get("/api/v0/info") or {}
        caps = info.get("capabilities") or {}
        count = int(self.opts["fan_count"] or 0) or int(caps.get("fan_count") or 0) or 10
        self.read_rpm()
        return ControllerInfo(name=caps.get("name") or "OpenFAN", fan_count=count,
                              hardware=str(info.get("hardware", "")), firmware=str(info.get("firmware", "")))

    def set_pwm(self, index: int, pwm: int) -> None:
        self._get(f"/api/v0/fan/{int(index)}/pwm", {"value": max(0, min(100, int(pwm)))})

    def read_fan_names(self) -> dict[int, str]:
        """Fan names (aliases) saved in the OpenFAN Controller app.

        Uses /api/v0/alias/all/get; if that fails, asks for each fan with /api/v0/alias/<n>/get.
        """
        try:
            data = self._get("/api/v0/alias/all/get")
            if not isinstance(data, dict):
                raise ControllerError("unexpected alias response")
            return _clean_names(data)
        except ControllerError as first_error:
            count = int(self.opts["fan_count"] or 0) or 10
            names: dict[int, str] = {}
            for idx in range(count):
                try:
                    alias = self._get(f"/api/v0/alias/{idx}/get")
                except ControllerError:
                    continue
                if isinstance(alias, dict):  # some versions wrap it: {"<idx>": "name"}
                    alias = alias.get(str(idx), next(iter(alias.values()), None))
                if isinstance(alias, str) and alias.strip():
                    names[idx] = alias.strip()[:60]
            if not names:
                raise first_error
            return names

    def read_rpm(self) -> dict[int, int]:
        data = self._get("/api/v0/fan/status") or {}
        out = {}
        for k, v in data.items():
            try:
                out[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return out


def _clean_names(data: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for key, value in data.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, str) and value.strip():
            out[idx] = value.strip()[:60]
    return out


# ---------------------------------------------------------------------------
class DemoController(ControllerDriver):
    type_name = "demo"
    title = "Demo (simulated)"
    description = "A simulated controller for trying the service without hardware."
    options = [Option("fan_count", "Number of fans", type="number", default=10)]

    def __init__(self, controller_id, options):
        super().__init__(controller_id, options)
        self._pwm: dict[int, int] = {}
        self._lock = threading.Lock()

    @property
    def target(self) -> str:
        return "simulated"

    def connect(self) -> ControllerInfo:
        return ControllerInfo(name="Demo controller", fan_count=int(self.opts["fan_count"] or 10),
                              hardware="simulated", firmware="demo")

    def set_pwm(self, index: int, pwm: int) -> None:
        with self._lock:
            self._pwm[index] = pwm

    def read_rpm(self) -> dict[int, int]:
        with self._lock:
            return {i: int(p * 19 + random.uniform(-15, 15)) if p else 0 for i, p in self._pwm.items()}


BUILTIN_DRIVERS = [OpenFanSerial, OpenFanRest, DemoController]
