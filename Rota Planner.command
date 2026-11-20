#!/bin/sh
# macOS: double-click to start Rota Planner. Keep this window open while you
# work; close it to stop the program.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. Install it from https://www.python.org/downloads/ and start again."
  printf "Press Enter to close."; read -r _
  exit 1
fi
exec python3 run.py "$@"
