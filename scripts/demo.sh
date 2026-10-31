#!/bin/sh
set -e
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python
fi
"$PY" -m escort serve --replay --pace-ms 900 --host 127.0.0.1 --port 8747 &
LISTENER=$!
trap 'kill "$LISTENER"' EXIT
cd panel
npm run dev
