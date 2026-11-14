#!/usr/bin/env bash
# Create .venv and install the service + development dependencies.
#   scripts/setup.sh            # uses python3 from PATH
#   PYTHON=python3.13 scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found. Install Python 3.11 or newer (3.13 recommended)." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "error: $("$PYTHON" --version) is too old; Python 3.11 or newer is required." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment in .venv ($("$PYTHON" --version))"
  "$PYTHON" -m venv .venv || {
    echo "error: could not create the venv. On Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  }
fi

.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements-dev.txt

echo
echo "Done. Next:"
echo "  scripts/run.sh --demo     # start with simulated hardware, open http://localhost:5340"
echo "  scripts/test.sh           # run the tests"
