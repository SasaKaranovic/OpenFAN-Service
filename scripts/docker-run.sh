#!/usr/bin/env bash
# Run the locally built image (see scripts/docker-build.sh).
#
#   scripts/docker-run.sh --mode demo                         simulated hardware (default)
#   scripts/docker-run.sh --mode usb --device /dev/ttyACM0    real controller over USB
#   scripts/docker-run.sh --mode rest --url http://192.168.1.10:3000
#   scripts/docker-run.sh --mode rest                         -> fake controller on this host
#                                                             (start scripts/fake_openfan_rest.py first)
# Options:
#   --tag TAG         image (default openfan-service:dev)
#   --name NAME       container name (default openfan-service-dev)
#   --port N          host port (default 5340)
#   --config DIR      config folder on the host (default dev-config/docker-<mode>)
#   --unraid DIR      mount DIR read-only at /unraid (folder containing disks.ini)
#   --fresh           delete the config folder first
#   --foreground      run attached (Ctrl+C stops it) instead of in the background
#   --stop            stop and remove the container, then exit
#   -- ARGS...        everything after "--" is passed to "docker run" unchanged
#                     (relative paths in -v/--volume are made absolute), e.g.
#                     scripts/docker-run.sh --mode demo -- -v ./dev-config/unraid:/unraid:ro -e OPENFAN_LOG_LEVEL=debug
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=demo; DEVICE=/dev/ttyACM0; URL="http://host.docker.internal:3000"; TAG=openfan-service:dev
NAME=openfan-service-dev; PORT=5340; CONFIG=""; UNRAID=""; FRESH=0; FG=0; STOP=0; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; PASS=("$@"); break ;;
    --mode) MODE="${2:?}"; shift ;;
    --device) DEVICE="${2:?}"; shift ;;
    --url) URL="${2:?}"; shift ;;
    --tag) TAG="${2:?}"; shift ;;
    --name) NAME="${2:?}"; shift ;;
    --port) PORT="${2:?}"; shift ;;
    --config) CONFIG="${2:?}"; shift ;;
    --unraid) UNRAID="${2:?}"; shift ;;
    --fresh) FRESH=1 ;;
    --foreground) FG=1 ;;
    --stop) STOP=1 ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

docker rm -f "$NAME" >/dev/null 2>&1 || true
if [ "$STOP" = 1 ]; then echo "Stopped $NAME"; exit 0; fi

case "$MODE" in demo|usb|rest) ;; *) echo "--mode must be demo, usb or rest" >&2; exit 2 ;; esac
docker image inspect "$TAG" >/dev/null 2>&1 || { echo "Image $TAG not found. Run scripts/docker-build.sh first." >&2; exit 1; }

CONFIG="${CONFIG:-dev-config/docker-$MODE}"
[ "$FRESH" = 1 ] && rm -rf "$CONFIG"
mkdir -p "$CONFIG"
CONFIG_ABS="$(cd "$CONFIG" && pwd)"

ARGS=(--name "$NAME" -p "$PORT:5340" -v "$CONFIG_ABS:/config"
      -e "PUID=$(id -u)" -e "PGID=$(id -g)" -e "TZ=${TZ:-Etc/UTC}"
      --add-host=host.docker.internal:host-gateway)
case "$MODE" in
  demo) ARGS+=(-e OPENFAN_DEMO=1) ;;
  usb)
    [ -e "$DEVICE" ] || { echo "error: $DEVICE does not exist. Check: ls -l /dev/ttyACM* /dev/serial/by-id/" >&2; exit 1; }
    ARGS+=(--device "$DEVICE:/dev/ttyACM0" -e OPENFAN_SERIAL_PORT=/dev/ttyACM0) ;;
  rest) ARGS+=(-e "OPENFAN_CONTROLLER_URL=$URL") ;;
esac
if [ -n "$UNRAID" ]; then
  ARGS+=(-v "$(cd "$UNRAID" && pwd):/unraid:ro")
fi

# Extra "docker run" arguments after "--". Docker needs absolute host paths for bind
# mounts, so "-v ./x:/y" / "--volume=../x:/y" are resolved against the repo folder.
abs_volume() {
  local spec="$1" src rest
  case "$spec" in
    .*:*) src="${spec%%:*}"; rest="${spec#*:}"
          mkdir -p "$src" 2>/dev/null || true
          echo "$(cd "$src" && pwd):$rest" ;;
    *) echo "$spec" ;;
  esac
}
i=0
while [ $i -lt ${#PASS[@]} ]; do
  a="${PASS[$i]}"
  case "$a" in
    -v|--volume)
      ARGS+=("$a"); i=$((i + 1))
      [ $i -lt ${#PASS[@]} ] && ARGS+=("$(abs_volume "${PASS[$i]}")") ;;
    --volume=*) ARGS+=("--volume=$(abs_volume "${a#--volume=}")") ;;
    *) ARGS+=("$a") ;;
  esac
  i=$((i + 1))
done

if [ "$FG" = 1 ]; then
  echo "Starting $NAME in the foreground: http://localhost:$PORT"
  exec docker run --rm "${ARGS[@]}" "$TAG"
fi

docker run -d --restart unless-stopped "${ARGS[@]}" "$TAG" >/dev/null
echo -n "Started $NAME, waiting for it to become healthy"
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    echo; echo "Ready: http://localhost:$PORT   (config: $CONFIG)"
    echo "Logs:  docker logs -f $NAME     Stop: scripts/docker-run.sh --stop"
    exit 0
  fi
  echo -n "."; sleep 2
done
echo; echo "The service did not answer within 60 s. Last log lines:" >&2
docker logs --tail 40 "$NAME" >&2
exit 1
