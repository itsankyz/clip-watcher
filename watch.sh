#!/usr/bin/env bash
# clip-watcher: record a live Twitch stream, read its chat anonymously and
# clip the moments where chat explodes, then subtitle the clips with Whisper.
# This wrapper just picks a Python interpreter and runs clip_watcher.py.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PY=""
for candidate in .venv/bin/python python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "error: python3 not found (install Python 3.9+)" >&2
  exit 1
fi

exec "$PY" clip_watcher.py "$@"
