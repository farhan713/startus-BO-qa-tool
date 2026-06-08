#!/usr/bin/env bash
# ============================================================================
# Stratus QA Tool — one-command launcher
#
# Starts:
#   • the mock Stratus server  (localhost:8080) — for the offline demo
#   • the Flask Web UI         (localhost:5050)
#
# Usage:
#   ./launch.sh              # starts both, opens browser
#   ./launch.sh --no-mock    # only start the Web UI (point at real Stratus)
#   ./launch.sh --no-open    # don't auto-open the browser
#   ./launch.sh stop         # stop whatever is running
# ============================================================================

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT/.pids"
mkdir -p "$PID_DIR"
MOCK_PID="$PID_DIR/mock.pid"
WEB_PID="$PID_DIR/web.pid"

# ---------------------------------------------------------- helpers

color()   { printf "\033[%sm%s\033[0m" "$1" "$2"; }
green()   { color "32" "$1"; }
red()     { color "31" "$1"; }
yellow()  { color "33" "$1"; }
blue()    { color "34" "$1"; }
bold()    { color "1" "$1"; }

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

stop_one() {
  local name="$1" pid_file="$2"
  if is_running "$pid_file"; then
    local pid; pid="$(cat "$pid_file")"
    kill "$pid" 2>/dev/null || true
    sleep 0.3
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pid_file"
    echo "  $(green '✓') stopped $name (pid $pid)"
  else
    rm -f "$pid_file" 2>/dev/null || true
    echo "  $(yellow '·') $name was not running"
  fi
}

stop_all() {
  echo
  bold "Stopping Stratus QA Tool servers…"; echo
  stop_one "mock Stratus" "$MOCK_PID"
  stop_one "Web UI"       "$WEB_PID"
  # Belt-and-braces: also kill anything matching by name
  pkill -f "mock_server/server.py" 2>/dev/null || true
  pkill -f "web_ui/app.py" 2>/dev/null || true
  echo
  exit 0
}

# ---------------------------------------------------------- arg parsing

NO_MOCK=0
NO_OPEN=0
for arg in "$@"; do
  case "$arg" in
    stop)        stop_all ;;
    --no-mock)   NO_MOCK=1 ;;
    --no-open)   NO_OPEN=1 ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *)
      echo "$(red 'Unknown option:') $arg"; exit 1 ;;
  esac
done

# ---------------------------------------------------------- preflight

cd "$ROOT"

if [[ ! -d ".venv" ]]; then
  echo "$(red '✗') .venv not found. Run one-time setup:"
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium"
  exit 1
fi

source .venv/bin/activate

if ! python -c "import flask, playwright" 2>/dev/null; then
  echo "$(red '✗') Required packages missing. Reinstall:"
  echo "   source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# ---------------------------------------------------------- banner

cat <<EOF

$(bold 'Stratus QA Tool — Web Console')
$(blue '──────────────────────────────────────────────')
EOF

# ---------------------------------------------------------- start mock

if [[ $NO_MOCK -eq 0 ]]; then
  if is_running "$MOCK_PID"; then
    echo "  $(yellow '·') mock Stratus already running (pid $(cat "$MOCK_PID"))"
  else
    nohup python mock_server/server.py > .pids/mock.log 2>&1 &
    echo $! > "$MOCK_PID"
    echo "  $(green '✓') mock Stratus  ← http://localhost:8080/backoffice/login.jsp"
  fi
else
  echo "  $(yellow '·') mock Stratus  → skipped (--no-mock)"
fi

# ---------------------------------------------------------- start web ui

if is_running "$WEB_PID"; then
  echo "  $(yellow '·') Web UI already running (pid $(cat "$WEB_PID"))"
else
  nohup python web_ui/app.py > .pids/web.log 2>&1 &
  echo $! > "$WEB_PID"
  echo "  $(green '✓') Web UI        ← http://localhost:5050"
fi

# ---------------------------------------------------------- wait & open

echo
echo "$(yellow 'Waiting for servers to respond…')"
for i in 1 2 3 4 5 6 7 8; do
  ok_mock=true; ok_web=true
  [[ $NO_MOCK -eq 0 ]] && ! curl -sf http://localhost:8080/backoffice/login.jsp > /dev/null && ok_mock=false
  curl -sf http://localhost:5050/ > /dev/null 2>&1 || ok_web=false
  if $ok_mock && $ok_web; then break; fi
  sleep 0.5
done

if curl -sf http://localhost:5050/ > /dev/null 2>&1; then
  echo "$(green '✓ Web UI is live at http://localhost:5050')"
else
  echo "$(red '✗ Web UI did not start — check .pids/web.log')"
  exit 1
fi

if [[ $NO_OPEN -eq 0 ]]; then
  if command -v open > /dev/null; then
    open "http://localhost:5050"
  elif command -v xdg-open > /dev/null; then
    xdg-open "http://localhost:5050"
  fi
fi

cat <<EOF

$(blue '──────────────────────────────────────────────')
$(bold 'Ready. Open ▶') $(green 'http://localhost:5050')

  Logs:
    .pids/mock.log     (mock Stratus)
    .pids/web.log      (Web UI)

  To stop everything:
    ./launch.sh stop

EOF
