#!/usr/bin/env bash
# Run the unit tests and a syntax check of the web UI JavaScript.
#   scripts/test.sh                 # everything
#   scripts/test.sh -k engine       # extra arguments go to pytest
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv yet. Run scripts/setup.sh first." >&2; exit 1; }

"$PY" -m pytest "$@"

if command -v node >/dev/null 2>&1; then
  echo "Checking web UI JavaScript syntax..."
  node scripts/check_js.mjs
else
  echo "(node not installed: skipped the JavaScript syntax check)"
fi
