#!/bin/sh
# Prepares /config and the serial device permissions, then runs the service as
# PUID:PGID so files on the host (Unraid appdata, TrueNAS dataset, ...) get the
# owner you expect and survive container re-creation.
set -e

CONFIG_DIR="${OPENFAN_CONFIG_DIR:-/config}"
umask "${UMASK:-022}"
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/backups" "$CONFIG_DIR/plugins"

if [ "$(id -u)" != "0" ]; then
  # Started with --user: nothing to fix, just run.
  exec "$@"
fi

PUID="${PUID:-99}"
PGID="${PGID:-100}"

if ! getent group openfan >/dev/null 2>&1; then
  groupadd -o -g "$PGID" openfan
else
  groupmod -o -g "$PGID" openfan 2>/dev/null || true
fi
if ! id openfan >/dev/null 2>&1; then
  useradd -o -u "$PUID" -g openfan -d "$CONFIG_DIR" -M -s /usr/sbin/nologin openfan
else
  usermod -o -u "$PUID" -g openfan openfan 2>/dev/null || true
fi

# Give the service access to USB serial devices passed into the container,
# whatever group owns them on the host.
for dev in /dev/ttyACM* /dev/ttyUSB* /dev/serial/by-id/*; do
  [ -e "$dev" ] || continue
  gid="$(stat -Lc %g "$dev")"
  grp="$(getent group "$gid" | cut -d: -f1)"
  if [ -z "$grp" ]; then
    grp="serial$gid"
    groupadd -o -g "$gid" "$grp"
  fi
  usermod -a -G "$grp" openfan
done

# Only touch files that are not already owned correctly (fast on big folders,
# and leaves anything else the user keeps in /config alone).
find "$CONFIG_DIR" -maxdepth 2 \( ! -user "$PUID" -o ! -group "$PGID" \) \
  \( -name 'config*.yaml' -o -name 'history.sqlite*' -o -name '.session-secret' -o -name '.config-*' \
     -o -path "$CONFIG_DIR" -o -path "$CONFIG_DIR/backups" -o -path "$CONFIG_DIR/plugins" \) \
  -exec chown "$PUID:$PGID" {} + 2>/dev/null || \
  echo "Note: could not change ownership in $CONFIG_DIR (read-only or network share?). Continuing."

echo "Starting OpenFAN Service as uid $PUID gid $PGID (config: $CONFIG_DIR)"
exec gosu openfan "$@"
