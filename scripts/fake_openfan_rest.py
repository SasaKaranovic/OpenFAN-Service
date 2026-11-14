#!/usr/bin/env python3
"""A fake OpenFAN Controller REST API for local testing without hardware.

It answers the same endpoints the real OpenFAN Controller app does
(/api/v0/info, /api/v0/fan/status, /api/v0/fan/<n>/pwm, /api/v0/alias/...), so you can test the
service's "OpenFAN over network (REST API)" controller type on any machine.

    python scripts/fake_openfan_rest.py               # listens on 0.0.0.0:3000
    python scripts/fake_openfan_rest.py --port 3001 --fans 10 --fail-after 120

--fail-after N   stop answering after N seconds (to test "controller unreachable")
--quiet          do not print every request

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATE = {"pwm": {}, "started": time.time()}
ALIASES = {0: "Front intake 1", 1: "Front intake 2", 2: "Front intake 3", 3: "Rear exhaust", 4: "Top exhaust",
           5: "CPU front", 6: "CPU rear", 7: "HBA fan"}
LOCK = threading.Lock()


def make_handler(args):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, status=200):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802 - http.server API
            if args.fail_after and time.time() - STATE["started"] > args.fail_after:
                # Simulate a dead controller: drop the connection without answering.
                self.close_connection = True
                return
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/api/v0/info":
                return self._send({"status": "ok", "message": "", "data": {
                    "hardware": "HW:3 MCU:PICO2040 (fake)", "firmware": "FW:fake",
                    "software": "fake_openfan_rest.py",
                    "capabilities": {"name": "OpenFAN Controller (fake)", "fan_count": args.fans}}})
            if url.path == "/api/v0/fan/status":
                with LOCK:
                    data = {str(i): (int(STATE["pwm"].get(i, 50) * 19 + random.uniform(-20, 20))
                                     if STATE["pwm"].get(i, 50) else 0) for i in range(args.fans)}
                return self._send({"status": "ok", "message": "", "data": data})
            if url.path == "/api/v0/alias/all/get":
                return self._send({"status": "ok", "message": "", "data": {
                    str(i): ALIASES.get(i, f"Fan #{i + 1}") for i in range(args.fans)}})
            m = re.fullmatch(r"/api/v0/alias/(\d+)/get", url.path)
            if m:
                i = int(m.group(1))
                return self._send({"status": "ok", "message": "", "data": ALIASES.get(i, f"Fan #{i + 1}")})
            m = re.fullmatch(r"/api/v0/fan/(\d+)/(pwm|set)", url.path)
            if m:
                idx = int(m.group(1))
                if idx >= args.fans:
                    return self._send({"status": "error", "message": "Invalid fan index", "data": None})
                try:
                    value = max(0, min(100, int(q.get("value", ["0"])[0])))
                except ValueError:
                    return self._send({"status": "error", "message": "Invalid value", "data": None})
                with LOCK:
                    STATE["pwm"][idx] = value
                return self._send({"status": "ok", "message": f"Fan {idx} -> {value}%", "data": None})
            return self._send({"status": "fail", "message": "Unsupported method"}, 404)

        def log_message(self, fmt, *a):
            if not args.quiet:
                print(f"[fake-openfan] {self.address_string()} {fmt % a}")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--fans", type=int, default=10)
    ap.add_argument("--fail-after", type=float, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(args))
    print(f"Fake OpenFAN REST API on http://{args.host}:{args.port} with {args.fans} fans (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
