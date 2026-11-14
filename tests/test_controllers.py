import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from openfan_service.controllers.base import ControllerError
from openfan_service.controllers.drivers import OpenFanProtocol, OpenFanRest, board_from_hw_info, parse_rpm_line


class FakeSerial:
    """Mimics the OpenFAN firmware on the other end of the USB cable."""

    def __init__(self, silent=False):
        self.written = []
        self.out = []
        self.silent = silent

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        self.written.append(data)
        cmd = data.decode().strip()
        if self.silent:
            return
        if cmd.startswith(">00"):
            self.out += [b"some debug line\r\n", b"<00|00:0320;01:01F4;02:0000;\r\n"]
        elif cmd.startswith(">02"):
            self.out.append(b"<02|ok\r\n")
        elif cmd.startswith(">05"):
            self.out += [b"<05|HW:3\r\n", b"MCU:PICO2040\r\n"]

    def readline(self):
        return self.out.pop(0) if self.out else b""

    def close(self):
        pass


def test_protocol_set_pwm_encoding():
    s = FakeSerial()
    OpenFanProtocol(s).set_pwm(3, 50)
    assert s.written[-1] == b">0203" + b"7F" + b"\r\n"   # 50% -> 127 -> 0x7F
    OpenFanProtocol(s).set_pwm(9, 100)
    assert s.written[-1] == b">0209FF\r\n"
    OpenFanProtocol(s).set_pwm(0, 250)                    # clamped
    assert s.written[-1] == b">0200FF\r\n"


def test_protocol_rpm_and_hw_info():
    s = FakeSerial()
    p = OpenFanProtocol(s)
    assert p.read_rpm() == {0: 800, 1: 500, 2: 0}
    hw = p.hw_info()
    assert board_from_hw_info(hw) == ("OpenFAN Controller", 10)


def test_protocol_timeout():
    p = OpenFanProtocol(FakeSerial(silent=True), timeout=0.2)
    with pytest.raises(ControllerError):
        p.read_rpm()


def test_parse_rpm_line_errors():
    with pytest.raises(ControllerError):
        parse_rpm_line("<garbage")
    assert parse_rpm_line("<00|00:zz;01:0010;") == {1: 16}


def test_board_detection():
    assert board_from_hw_info("MCU:OpenFAN_Micro_ESP32C3")[1] == 1
    assert board_from_hw_info("MCU:PICO235")[0] == "OpenFAN XL"
    assert board_from_hw_info("???")[1] is None


class FakeController(BaseHTTPRequestHandler):
    calls = []
    no_all_alias = False

    def do_GET(self):
        FakeController.calls.append(self.path)
        if self.path.startswith("/api/v0/info"):
            body = {"status": "ok", "data": {"hardware": "MCU:PICO2040", "firmware": "1",
                                             "capabilities": {"name": "OpenFAN Controller", "fan_count": 10}}}
        elif self.path.startswith("/api/v0/fan/status"):
            body = {"status": "ok", "data": {"0": 900, "1": 0}}
        elif "/pwm" in self.path:
            body = {"status": "ok", "data": None}
        elif self.path == "/api/v0/alias/all/get" and not FakeController.no_all_alias:
            body = {"status": "ok", "data": {"0": "Front intake", "1": " Rear ", "2": "", "x": "bad"}}
        elif self.path.startswith("/api/v0/alias/") and self.path.endswith("/get") and "all" not in self.path:
            idx = int(self.path.split("/")[4])
            body = {"status": "ok", "data": f"Alias {idx}"}
        else:
            body = {"status": "fail", "message": "nope"}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def rest_server():
    srv = HTTPServer(("127.0.0.1", 0), FakeController)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_rest_driver(rest_server):
    d = OpenFanRest("main", {"url": rest_server})
    info = d.connect()
    assert info.fan_count == 10 and info.name == "OpenFAN Controller"
    d.set_pwm(4, 55)
    assert "/api/v0/fan/4/pwm?value=55" in FakeController.calls
    assert d.read_rpm() == {0: 900, 1: 0}


def test_rest_driver_unreachable():
    d = OpenFanRest("main", {"url": "http://127.0.0.1:9", "timeout": 1})
    with pytest.raises(ControllerError):
        d.connect()


def test_rest_fan_names(rest_server):
    FakeController.no_all_alias = False
    d = OpenFanRest("main", {"url": rest_server})
    assert d.supports_fan_names
    assert d.read_fan_names() == {0: "Front intake", 1: "Rear"}


def test_rest_fan_names_fallback_per_fan(rest_server):
    FakeController.no_all_alias = True
    try:
        d = OpenFanRest("main", {"url": rest_server, "fan_count": 3})
        assert d.read_fan_names() == {0: "Alias 0", 1: "Alias 1", 2: "Alias 2"}
    finally:
        FakeController.no_all_alias = False


def test_serial_cannot_read_fan_names():
    from openfan_service.controllers.drivers import OpenFanSerial
    assert not OpenFanSerial.supports_fan_names
    with pytest.raises(ControllerError):
        OpenFanSerial("main", {}).read_fan_names()
