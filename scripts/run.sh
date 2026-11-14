#!/usr/bin/env bash
# Run OpenFAN Service from source (no Docker).
#
#   scripts/run.sh --demo                         simulated controller + sensors (default)
#   scripts/run.sh --serial auto                  real OpenFAN over USB (auto-detect)
#   scripts/run.sh --serial /dev/ttyACM0
#   scripts/run.sh --rest http://192.168.1.10:3000
#
# Options:
#   --config DIR    config folder (default: dev-config/<mode>)
#   --port N        web UI port (default 5340)
#   --fresh         delete the config folder first (start like a new install)
#   --debug         debug logging
#
# The mode (--demo/--serial/--rest) only seeds a NEW config folder. Once
# config.yaml exists, change controllers in the web UI or use --fresh.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=demo; SERIAL=auto; REST_URL=""; CONFIG=""; PORT=5340; FRESH=0; LEVEL=info
while [ $# -gt 0 ]; do
  case "$1" in
    --demo) MODE=demo ;;
    --serial) MODE=serial; SERIAL="${2:-auto}"; shift ;;
    --rest) MODE=rest; REST_URL="${2:?--rest needs a URL}"; shift ;;
    --config) CONFIG="${2:?}"; shift ;;
    --port) PORT="${2:?}"; shift ;;
    --fresh) FRESH=1 ;;
    --debug) LEVEL=debug ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    --|-v|--volume|-e)
      echo "error: run.sh starts the service from source with Python, not in Docker, so Docker options" >&2
      echo "       like '-v host:container' do not apply. Point sensor sources at host paths directly" >&2
      echo "       (e.g. $(pwd)/dev-config/unraid/disks.ini), or use scripts/docker-run.sh to run in Docker." >&2
      exit 2 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv yet. Run scripts/setup.sh first." >&2; exit 1; }

CONFIG="${CONFIG:-dev-config/$MODE}"
if [ "$FRESH" = 1 ] && [ -d "$CONFIG" ]; then
  echo "Removing $CONFIG (--fresh)"
  rm -rf "$CONFIG"
fi
mkdir -p "$CONFIG"

export OPENFAN_CONFIG_DIR="$CONFIG" OPENFAN_PORT="$PORT" OPENFAN_LOG_LEVEL="$LEVEL"
unset OPENFAN_DEMO OPENFAN_SERIAL_PORT OPENFAN_CONTROLLER_URL
case "$MODE" in
  demo) export OPENFAN_DEMO=1 ;;
  serial) export OPENFAN_SERIAL_PORT="$SERIAL" ;;
  rest) export OPENFAN_CONTROLLER_URL="$REST_URL" ;;
esac

if [ -f "$CONFIG/config.yaml" ]; then
  echo "Using existing config in $CONFIG (mode flags only apply to a new config; add --fresh to reset)"
fi
echo "OpenFAN Service: http://localhost:$PORT   (config: $CONFIG, Ctrl+C to stop)"
exec "$PY" -m openfan_service
