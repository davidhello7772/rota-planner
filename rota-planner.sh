#!/bin/sh
# Linux: start Rota Planner (double-click, or run from a terminal).
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed (package python3)."
  exit 1
fi
exec python3 run.py "$@"
