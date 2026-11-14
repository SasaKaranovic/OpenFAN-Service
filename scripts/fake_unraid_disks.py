#!/usr/bin/env python3
"""Write a fake Unraid disks.ini that changes over time.

Lets you test the "Unraid disks" sensor source on Windows or Linux without an
Unraid server. The file format matches /var/local/emhttp/disks.ini.

    python scripts/fake_unraid_disks.py                       # ./dev-config/unraid/disks.ini
    python scripts/fake_unraid_disks.py --out C:/temp/disks.ini --interval 5
    python scripts/fake_unraid_disks.py --sleep disk2 --remove-after 60 --remove disk3

--sleep NAME         report this disk as spun down ("*"), can repeat
--remove NAME        drop this disk from the file after --remove-after seconds
                     (tests the "sensor disappeared -> fail-safe" path)
--freeze-after N     stop updating the file after N seconds (tests stale detection
                     when the source uses "Max file age")

Point a sensor source of type "Unraid disks" at the file. From Docker, mount the
folder that contains it, e.g. -v ./dev-config/unraid:/unraid:ro and use
/unraid/disks.ini.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import tempfile
import time
from pathlib import Path

DISKS = [("parity", 33), ("disk1", 35), ("disk2", 37), ("disk3", 39), ("cache", 45)]


def render(t: float, sleeping: set[str], removed: set[str]) -> str:
    out = []
    for i, (name, base) in enumerate(DISKS):
        if name in removed:
            continue
        temp = "*" if name in sleeping else str(round(base + 5 * math.sin(t / (60 + i * 17)) + random.uniform(-0.5, 0.5)))
        out += [f'["{name}"]', f'name="{name}"', f'id="FAKE_DISK_{i:02d}"', f'temp="{temp}"', 'status="DISK_OK"']
    out += ['["disk9"]', 'name="disk9"', 'temp="*"', 'status="DISK_NP"']  # empty slot, ignored
    return "\n".join(out) + "\n"


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".disks-")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="dev-config/unraid/disks.ini")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--sleep", action="append", default=[])
    ap.add_argument("--remove", action="append", default=[])
    ap.add_argument("--remove-after", type=float, default=60)
    ap.add_argument("--freeze-after", type=float, default=0)
    args = ap.parse_args()

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    print(f"Writing {path.resolve()} every {args.interval:g} s (Ctrl+C to stop)")
    try:
        while True:
            t = time.time() - start
            if args.freeze_after and t > args.freeze_after:
                print("Frozen: no more updates.")
                while True:
                    time.sleep(3600)
            removed = set(args.remove) if t > args.remove_after else set()
            write_atomic(path, render(t, set(args.sleep), removed))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
