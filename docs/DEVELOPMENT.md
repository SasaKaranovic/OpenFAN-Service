# Development guide

This guide explains how to run OpenFAN Service from source, run the tests, build the Docker image on your own machine, and test without hardware. Every task has a script for Linux/macOS (`scripts/*.sh`) and a matching one for Windows (`scripts/*.ps1`). The PowerShell scripts work in both Windows PowerShell 5.1 and PowerShell 7. They also accept the Linux spelling of every option, so `scripts\run.ps1 --rest URL --port 5341` and `scripts\run.ps1 -Rest URL -Port 5341` do the same thing.

| Task | Linux / macOS | Windows (PowerShell) |
|---|---|---|
| Create `.venv` and install dependencies | `scripts/setup.sh` | `scripts\setup.ps1` |
| Run from source | `scripts/run.sh --demo` | `scripts\run.ps1 -Demo` |
| Run the tests | `scripts/test.sh` | `scripts\test.ps1` |
| Build the Docker image | `scripts/docker-build.sh` | `scripts\docker-build.ps1` |
| Run the Docker image | `scripts/docker-run.sh --mode demo` | `scripts\docker-run.ps1 -Mode demo` |
| Stop the container | `scripts/docker-run.sh --stop` | `scripts\docker-run.ps1 -Stop` |

There are also three helper tools in `scripts/`. They need only Python, with no extra packages:

| Tool | What it does |
|---|---|
| `fake_openfan_rest.py` | A fake OpenFAN Controller REST API on port 3000, including fan names, so you can test REST mode and *Import fan names* without a board |
| `fake_unraid_disks.py` | Writes a changing Unraid `disks.ini`, so you can test the Unraid plugin without Unraid |
| `push_sensor.py` | Sends values to a Push sensor source |

All local data goes into `dev-config/`, which git ignores. Each mode gets its own sub-folder (`dev-config/demo`, `dev-config/serial`, `dev-config/docker-demo`, ...), so the modes never mix.

---

## 1. Prerequisites

| | Linux | Windows |
|---|---|---|
| Python 3.11+ (3.13 recommended) | `sudo apt install python3 python3-venv` (Debian/Ubuntu) | [python.org](https://www.python.org/downloads/): tick *Add python.exe to PATH* |
| Docker (for image builds) | [Docker Engine](https://docs.docker.com/engine/install/) | [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend |
| Node.js (optional) | Used by the test script for a JavaScript syntax check | Same |

### Windows: allow the scripts to run (one time)

Windows blocks downloaded scripts by default. Use either of these:

```powershell
# For your user only, permanently:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# ...or per call:
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

If Windows still complains that a file is "not digitally signed", unblock the folder once:

```powershell
Get-ChildItem -Recurse scripts | Unblock-File
```

---

## 2. Run from source

```sh
scripts/setup.sh              # Windows: scripts\setup.ps1
scripts/run.sh --demo         # Windows: scripts\run.ps1 -Demo
```

Open <http://localhost:5340>. Demo mode uses a simulated controller with 10 fans, a simulated sensor source, and one example curve.

### Modes

| Linux | Windows | Controller |
|---|---|---|
| `scripts/run.sh --demo` | `scripts\run.ps1 -Demo` | Simulated |
| `scripts/run.sh --serial auto` | `scripts\run.ps1 -Serial auto` | Real board over USB, auto-detected |
| `scripts/run.sh --serial /dev/ttyACM0` | `scripts\run.ps1 -Serial COM3` | Real board on a specific port |
| `scripts/run.sh --rest http://192.168.1.10:3000` | `scripts\run.ps1 -Rest http://192.168.1.10:3000` | OpenFAN Controller app (REST API) |

Other options:

| Linux | Windows | Meaning |
|---|---|---|
| `--port 5341` | `-Port 5341` | Use another web port |
| `--config DIR` | `-Config DIR` | Use another config folder |
| `--fresh` | `-Fresh` | Delete the config folder first, as on a new install |
| `--debug` | `-DebugLog` | Debug logging |

> The mode only seeds a **new** config folder. Once `config.yaml` exists, the controller comes from that file. Change it on the *Controllers* page, or start over with `--fresh` / `-Fresh`.

`run` starts the service directly with Python, **not in Docker**, so Docker options such as `-v host:container` don't apply and are rejected with an explanation. Sensor sources read host paths directly. For example, with `fake_unraid_disks.py` running, add an *Unraid disks* source with the full path to `dev-config/unraid/disks.ini`. To run in a container with mounted folders, use `docker-run` (section 5).

Running from source is the easiest way to use a **real controller on Windows**. pyserial sees COM ports directly, so `-Serial auto` finds the board without any USB passthrough.

Stop the service with **Ctrl+C**. On stop, managed fans are set to 100% (the *Speed when the service stops* setting).

### Without the scripts

```sh
python -m venv .venv
. .venv/bin/activate                  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
OPENFAN_DEMO=1 OPENFAN_CONFIG_DIR=./dev-config/demo python -m openfan_service
```

In PowerShell:

```powershell
$env:OPENFAN_DEMO = '1'
$env:OPENFAN_CONFIG_DIR = '.\dev-config\demo'
python -m openfan_service
```

All environment variables are listed in the [README](../README.md#environment-variables).

### Editing the web UI

The UI is plain HTML, CSS and JavaScript modules in `openfan_service/web/`. There is no build step. Save the file and reload the browser (Ctrl+F5 if the browser cached the old version). Python changes need a restart of `run.sh` / `run.ps1`.

---

## 3. Tests

```sh
scripts/test.sh                # Windows: scripts\test.ps1
scripts/test.sh -k engine      # extra arguments go to pytest
scripts/test.sh -x -vv
```

The script runs pytest (`tests/`). If Node.js is installed, it also syntax-checks every JavaScript file of the web UI. The tests need no hardware, network or Docker. Serial and REST are tested against a simulated board and a local fake server.

| File | Covers |
|---|---|
| `test_curves.py` | Curve maths, hysteresis, clamping |
| `test_config.py` | Validation, atomic save, backups, recovery from a broken file |
| `test_sensors.py` | Plugins (Unraid, file, command, hwmon, push), staleness, user plugins |
| `test_engine.py` | Fail-safe and recovery, notifications, startup, override, exit speed, history |
| `test_controllers.py` | Serial protocol encoding/parsing, REST driver |
| `test_api.py` | HTTP API end to end with the demo hardware |

---

## 4. Build the Docker image

```sh
scripts/docker-build.sh                 # Windows: scripts\docker-build.ps1
```

This builds `openfan-service:dev` for your machine's architecture. Options:

| Linux | Windows | Meaning |
|---|---|---|
| `--tag NAME:TAG` | `-Tag NAME:TAG` | Another image name |
| `--no-cache` | `-NoCache` | Rebuild every layer |
| `--multiarch` | `-MultiArch` | Also build `linux/amd64` and `linux/arm64` to check that both work. Nothing is pushed or loaded; the result stays in the build cache. |

Every build is stamped with a version: the build date (UTC) and the git short hash, e.g. `v2026-11-13 [a1b2c3d]`. `-dirty` is added when tracked files have uncommitted changes. The version appears in the page footer and `/api/health`. The script tags the image twice: `openfan-service:dev` and `openfan-service:2026-11-13-a1b2c3d`.

Without the script, pass the version yourself. Otherwise the footer shows `vdev [unknown]`:

```sh
docker build -t openfan-service:dev \
  --build-arg OPENFAN_VERSION=$(date -u +%Y-%m-%d) \
  --build-arg OPENFAN_GIT_HASH=$(git rev-parse --short HEAD) .
```

When running from source, the footer shows the date and hash of the last commit, plus `-dirty` if there are uncommitted changes. Before the first commit it shows `vdev [unknown]`.

### Multi-arch notes

- Docker Desktop (Windows/macOS) can build `arm64` on an x86 PC out of the box.
- On Linux, install the QEMU emulators once:
  ```sh
  docker run --privileged --rm tonistiigi/binfmt --install arm64
  ```
- Publishing multi-arch images to ghcr.io is done by the GitHub workflow (`.github/workflows/docker_image.yaml`), not by these scripts.

---

## 5. Run the Docker image

```sh
scripts/docker-run.sh --mode demo        # Windows: scripts\docker-run.ps1 -Mode demo
```

The script:

1. Removes any old `openfan-service-dev` container.
2. Starts a new one in the background with `restart: unless-stopped`.
3. Mounts `dev-config/docker-<mode>` as `/config`.
4. Waits until `/api/health` answers, then prints the URL.

| Linux | Windows | Meaning |
|---|---|---|
| `--mode demo` | `-Mode demo` | Simulated hardware |
| `--mode rest --url URL` | `-Mode rest -Url URL` | REST controller. The default URL is `http://host.docker.internal:3000`, i.e. something running on your PC. |
| `--mode usb --device /dev/ttyACM0` | `-Mode usb -Device /dev/ttyACM0` | Real board over USB. On Windows, see section 7 first. |
| `--unraid DIR` | `-Unraid DIR` | Mount `DIR` read-only at `/unraid` (for a fake `disks.ini`) |
| `--fresh` | `-Fresh` | Start with an empty config folder |
| `--foreground` | `-Foreground` | Run attached; Ctrl+C stops it |
| `--stop` | `-Stop` | Stop and remove the container |
| `--port`, `--name`, `--tag`, `--config` | `-Port`, `-Name`, `-Tag`, `-Config` | As named |
| `-- ARGS...` | `-DockerArgs ...` or `-- ARGS...` | Pass any extra arguments to `docker run` (volumes, env vars, devices...). Relative host paths in `-v`/`--volume` are made absolute. |

Extra `docker run` arguments, for example mounting the fake Unraid folder and turning on debug logging:

```sh
scripts/docker-run.sh --mode demo -- -v ./dev-config/unraid:/unraid:ro -e OPENFAN_LOG_LEVEL=debug
```

```powershell
scripts\docker-run.ps1 -Mode demo -- -v .\dev-config\unraid:/unraid:ro -e OPENFAN_LOG_LEVEL=debug
# or, equivalently:
scripts\docker-run.ps1 -Mode demo -DockerArgs '-v', '.\dev-config\unraid:/unraid:ro', '-e', 'OPENFAN_LOG_LEVEL=debug'
```

> In PowerShell, the `--` is required before raw docker options. Without it, PowerShell reads `-v` as its own `-Verbose` switch.

Useful commands:

```sh
docker logs -f openfan-service-dev          # follow the log
docker exec -it openfan-service-dev sh      # shell inside the container
docker inspect --format '{{.State.Health.Status}}' openfan-service-dev
```

### With docker compose

`docker-compose.dev.yml` builds from this folder and runs demo mode:

```sh
docker compose -f docker-compose.dev.yml up --build
docker compose -f docker-compose.dev.yml down
```

### File permissions (Linux)

On Linux, `docker-run.sh` passes your own user and group id as `PUID`/`PGID`, so the files in `dev-config/` belong to you. With other values, files in `/config` belong to that user. Unraid uses `99:100` and TrueNAS uses `568:568`. On Windows, Docker Desktop handles ownership itself, so the values do not matter there.

---

## 6. Testing without hardware

### Fake REST controller

```sh
python scripts/fake_openfan_rest.py                 # http://0.0.0.0:3000, 10 fans
python scripts/fake_openfan_rest.py --fail-after 120 --quiet
```

`--fail-after` stops answering after that many seconds. Use it to see the *controller unreachable* alert, which fires after the stale timeout (60 s by default), and the reconnect message when you restart the fake.

Point the service at it in either of these ways:

- From source: `scripts/run.sh --rest http://127.0.0.1:3000`
- From Docker: `scripts/docker-run.sh --mode rest` (it reaches your PC as `host.docker.internal`)

> On Windows, the firewall may ask whether Python may accept connections. Allow *private networks* so the container can reach it.

### Fake Unraid disks

```sh
python scripts/fake_unraid_disks.py                                   # writes dev-config/unraid/disks.ini
python scripts/fake_unraid_disks.py --sleep parity                    # parity spun down ("*")
python scripts/fake_unraid_disks.py --remove disk3 --remove-after 60  # disk3 disappears after 60 s
python scripts/fake_unraid_disks.py --freeze-after 120                # file stops changing after 120 s
```

Then add a sensor source of type **Unraid disks**:

- From source, the path is the absolute path of `dev-config/unraid/disks.ini`.
- From Docker, start the container with `--unraid dev-config/unraid` (Windows: `-Unraid dev-config\unraid`), or the general form `-- -v ./dev-config/unraid:/unraid:ro`, and use the path `/unraid/disks.ini`.

What to expect:

- A removed disk, or a frozen file with *Max file age* set, puts the fans that use it in fail-safe once the stale timeout (default 60 s) has passed. Until then, the last good value is used.
- You can shorten the stale timeout under *Backup & settings → Engine* to see this faster.

### Push values

1. Create a sensor source of type **Push** with id `script`.
2. Send values:

```sh
python scripts/push_sensor.py --source script cpu=52.5 gpu=61
python scripts/push_sensor.py --source script --loop 5 cpu=random:40:70     # every 5 s
python scripts/push_sensor.py --url http://nas:5340 --token s3cret --source script cpu=50
```

Stop the loop and the sensor goes stale after the stale timeout.

### Notifications

Use a free [ntfy.sh](https://ntfy.sh) topic: add `ntfys://ntfy.sh/<a-random-topic>` on the *Notifications* page, press *Send test message*, and watch `https://ntfy.sh/<a-random-topic>` in the browser or the ntfy app. Then trigger a fail-safe, for example by deleting a curve that has fans assigned.

---

## 7. Optional: USB controller in Docker on Windows (usbipd-win)

Docker Desktop on Windows runs containers inside WSL 2, which cannot see USB devices on its own. [usbipd-win](https://github.com/dorssel/usbipd-win) forwards a USB device from Windows into WSL 2, where Docker can use it.

> Simpler alternatives on Windows: run from source (`scripts\run.ps1 -Serial auto`), or use REST mode. USB passthrough is only needed to test the container itself with the real board.

1. **Install usbipd-win**, then reopen PowerShell:
   ```powershell
   winget install --exact dorssel.usbipd-win
   ```
2. **Find the OpenFAN board.** Run this in an **Administrator** PowerShell:
   ```powershell
   usbipd list
   ```
   Look for VID:PID `2e8a:000a` (OpenFAN Controller) and note its BUSID, e.g. `2-3`.
3. **Share it once** (Administrator). It stays shared after reboots:
   ```powershell
   usbipd bind --busid 2-3
   ```
4. **Attach it to WSL 2.** You need to do this again after every replug or reboot:
   ```powershell
   usbipd attach --wsl --busid 2-3
   ```
   - Add `--auto-attach` to keep re-attaching it automatically while that window is open.
   - While it is attached, Windows programs (including the OpenFAN Web Serial app) cannot use the board.
5. **Check the device name** in WSL:
   ```powershell
   wsl -e sh -c "ls -l /dev/ttyACM* /dev/serial/by-id/"
   ```
6. **Run the container:**
   ```powershell
   scripts\docker-run.ps1 -Mode usb -Device /dev/ttyACM0 -Fresh
   ```
7. **Give the board back to Windows** when you are done:
   ```powershell
   usbipd detach --busid 2-3
   ```

If the container reports *no OpenFAN board found* or *cannot open /dev/ttyACM0*:

- Check that step 4 printed no errors and that step 5 lists the device.
- Some WSL kernels name the device `/dev/ttyUSB0` instead. Pass that with `-Device`.
- If Docker Desktop still cannot open the device, install Docker Engine inside your WSL distribution (Ubuntu) instead and run `scripts/docker-run.sh --mode usb` from there. This setup is the most reliable for USB devices.

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `running scripts is disabled on this system` | See *Windows: allow the scripts to run* above |
| `No .venv yet` | Run `scripts/setup.sh` / `scripts\setup.ps1` |
| `ensurepip is not available` (Linux) | `sudo apt install python3-venv` |
| A folder named `--port` (or similar) appeared | An older version of `run.ps1` read Linux-style options as positional values. Update the scripts and delete the stray folder. |
| `Docker options like -v do not apply` from `run` | `run` does not use Docker. Use `docker-run` to mount folders. |
| Port 5340 already in use | `--port 5341` / `-Port 5341`, or stop the other instance (`scripts/docker-run.sh --stop`) |
| Changed `--serial`/`--rest`, but the old controller is still used | The mode only seeds a new config. Use `--fresh`, or change it on the *Controllers* page. |
| `cannot open /dev/ttyACM0: Permission denied` (from source, Linux) | `sudo usermod -aG dialout $USER`, then log out and back in |
| Serial port busy | Only one program can use it. Close the OpenFAN web app, a serial monitor, or another service instance. |
| Docker: `The Docker engine is not running` | Start Docker Desktop and wait for *Engine running* |
| Docker on Linux: `permission denied ... docker.sock` | `sudo usermod -aG docker $USER`, then log out and back in, or use `sudo` |
| Container unhealthy / restarting | `docker logs openfan-service-dev`. A broken `config.yaml` is reported there, and the service falls back to the newest backup. |
| UI shows an old version after an update | Hard reload (Ctrl+F5) |
| `entrypoint.sh: not found` or `\r` errors when the container starts | The script got Windows line endings. `.gitattributes` prevents this on new checkouts. To fix a checkout: `git rm --cached -r . && git reset --hard` (this discards uncommitted changes). |

---

## 9. Project layout

```
openfan_service/        the service (see README "Development" for a module overview)
  web/                  web UI (no build step)
tests/                  pytest suite
scripts/                setup/run/test/docker scripts + fake hardware tools
docker/entrypoint.sh    PUID/PGID handling, then runs the service
examples/               compose files, Unraid template, example plugin
docs/                   this guide
dev-config/             local data from the scripts (git-ignored)
```
