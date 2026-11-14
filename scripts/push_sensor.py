#!/usr/bin/env python3
"""Send sensor values to a "Push (API)" sensor source.

    python scripts/push_sensor.py --source script cpu=52.5 gpu=61
    python scripts/push_sensor.py --source script --loop 5 cpu=random:40:60
    python scripts/push_sensor.py --url http://nas:5340 --token s3cret --source script cpu=50

A value of random:LOW:HIGH sends a new random number each time (with --loop).
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request


def values(specs):
    out = {}
    for spec in specs:
        name, _, val = spec.partition("=")
        if val.startswith("random:"):
            _, lo, hi = val.split(":")
            out[name] = round(random.uniform(float(lo), float(hi)), 1)
        else:
            out[name] = float(val)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:5340")
    ap.add_argument("--source", required=True, help="id of the push sensor source")
    ap.add_argument("--token", default="")
    ap.add_argument("--loop", type=float, default=0, help="repeat every N seconds")
    ap.add_argument("values", nargs="+", help="name=value pairs")
    args = ap.parse_args()

    while True:
        body = json.dumps(values(args.values)).encode()
        req = urllib.request.Request(f"{args.url.rstrip('/')}/api/push/{args.source}", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        if args.token:
            req.add_header("X-OpenFAN-Token", args.token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(body.decode(), "->", resp.read().decode())
        except urllib.error.HTTPError as exc:
            print(body.decode(), "->", exc.code, exc.read().decode(), file=sys.stderr)
        except urllib.error.URLError as exc:
            print(f"cannot reach {args.url}: {exc.reason}", file=sys.stderr)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
