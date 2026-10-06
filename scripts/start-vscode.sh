#!/usr/bin/env bash
# Launch VS Code in the browser (code-server) with this repo open.
#
# Usage:  bash scripts/start-vscode.sh [port]      (default port: 8080)
#
# SANDBOX NOTE: this environment wipes /tmp and recycles running processes
# between sessions, so this script rebuilds code-server from the npm registry
# whenever it is missing (takes ~3 minutes) and then serves it on 0.0.0.0 so the
# live-preview proxy can reach it. It also defines a `code` command that opens a
# folder inside the running instance (like `code .` on a normal machine).
set -euo pipefail

PORT="${1:-8080}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CS_DIR=/tmp/cs-install/node_modules/code-server
VSCODE_DIR="${CS_DIR}/lib/vscode"
NODE_GYP="$(npm root -g)/npm/node_modules/node-gyp/bin/node-gyp.js"
# Modules code-server actually loads at runtime (kerberos/MongoDB is not one).
FIRST_PARTY=(node-pty @vscode/spdlog @vscode/native-watchdog @vscode/deviceid @vscode/sqlite3 @parcel/watcher)

if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":${PORT} "; then
  echo "VS Code is already listening on port ${PORT}."
  exit 0
fi

if [ ! -f "${CS_DIR}/out/node/entry.js" ]; then
  echo "code-server is not installed (or /tmp was wiped). Installing; this takes a few minutes..."
  mkdir -p /tmp/cs-install
  cd /tmp/cs-install
  [ -f package.json ] || npm init -y >/dev/null
  npm install code-server --ignore-scripts --no-audit --no-fund --loglevel=error

  cd "${VSCODE_DIR}"
  env npm_config_nodedir=/usr/local npm install --unsafe-perm --omit=dev \
    --ignore-scripts --no-audit --no-fund --loglevel=error

  for mod in "${FIRST_PARTY[@]}"; do
    echo "  building native module: ${mod}"
    (cd "node_modules/${mod}" && env npm_config_nodedir=/usr/local \
      node "${NODE_GYP}" rebuild >/dev/null)
  done

  (cd extensions && npm install --unsafe-perm --omit=dev --ignore-scripts \
    --no-audit --no-fund --loglevel=error)

  # VS Code needs a ripgrep binary; the sandbox already ships one.
  mkdir -p node_modules/@vscode/ripgrep/bin
  ln -sf "$(command -v rg)" node_modules/@vscode/ripgrep/bin/rg

  # `code <folder>` / `code .` -> open that folder in the running instance.
  # The client only attaches when invoked with a bare path and no flags, and it
  # locates the server through the session socket in the default state dir, so
  # keep the wrapper flag-free (config comes from CODE_SERVER_CONFIG instead).
  cat > /usr/local/bin/code <<'EOF'
#!/usr/bin/env bash
# Open a folder in the running browser VS Code instance:  code [folder]
CS=/tmp/cs-install/node_modules/code-server
FOLDER="$(cd "${1:-.}" 2>/dev/null && pwd)" || { echo "No such folder: ${1}"; exit 1; }
export CODE_SERVER_CONFIG=/tmp/cs-config/config.yaml

# Attaching requires the VS Code tab to be open; time-box it (with a SIGKILL
# fallback) so this can never hang the terminal.
OUT="$(timeout -k 3 12 node "$CS/out/node/entry.js" "$FOLDER" 2>&1)"
STATUS=$?

if [ $STATUS -eq 0 ] && ! grep -qi "error" <<<"$OUT"; then
  echo "Opened ${FOLDER} in VS Code."
  exit 0
fi

if [ $STATUS -eq 124 ] || [ $STATUS -eq 137 ] || grep -qiE "EADDRINUSE|address already in use" <<<"$OUT"; then
  echo "VS Code is running, but switching folders only works while its window is open."
  echo "  - Open the VS Code preview tab, then run:  code ${FOLDER}"
  echo "  - Or inside VS Code: Ctrl+Shift+P -> File: Open Folder... -> ${FOLDER}"
  exit 1
fi

printf '%s\n' "$OUT" | tail -5
exit 1
EOF
  chmod +x /usr/local/bin/code
  echo "code-server installed (the 'code' command is available)."
fi

# Keep code-server's generated password out of the default state directory.
mkdir -p /tmp/cs-config
cat > /tmp/cs-config/config.yaml <<'YAML'
auth: none
cert: false
YAML

echo "Starting VS Code, listening on 0.0.0.0:${PORT} (all interfaces; folder: ${REPO_DIR}) ..."
echo "Open the forwarded preview link for port ${PORT} (there is no login screen)."
cd "${REPO_DIR}"
exec node "${CS_DIR}/out/node/entry.js" \
  --config /tmp/cs-config/config.yaml \
  --bind-addr "0.0.0.0:${PORT}" \
  --disable-telemetry \
  --disable-update-check \
  "${REPO_DIR}"
