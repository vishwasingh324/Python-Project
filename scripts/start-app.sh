#!/usr/bin/env bash
# Start the Alexa assistant web app (backend.py) on 0.0.0.0.
#
# Usage:  bash scripts/start-app.sh [port]     (default port: 8000)
#
# Binds to 0.0.0.0 so the sandbox/preview proxy can reach it. Only the Python
# standard library is required, so this works in any checkout of the repo.
set -euo pipefail

PORT="${1:-8000}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":${PORT} "; then
  echo "The assistant is already listening on port ${PORT}."
  exit 0
fi

cd "${REPO_DIR}"
echo "Starting the assistant on http://0.0.0.0:${PORT} ..."
exec python backend.py --port "${PORT}"
