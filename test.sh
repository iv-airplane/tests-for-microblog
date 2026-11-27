#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MICROBLOG_DIR="${MICROBLOG_DIR:-$SCRIPT_DIR/../microblog}"
MICROBLOG_PYTHON="${MICROBLOG_PYTHON:-3.11}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-5000}"
export API_BASE_URL="${API_BASE_URL:-http://${API_HOST}:${API_PORT}}"
REWARD_FILE="${REWARD_FILE:-/logs/verifier/reward.txt}"

TMP_DIR="$(mktemp -d)"
SERVER_LOG="$TMP_DIR/server.log"
SERVER_PID_FILE="$TMP_DIR/server.pid"
SERVER_WRAPPER_PID=""

cleanup() {
    if [[ -f "$SERVER_PID_FILE" ]]; then
        kill "$(cat "$SERVER_PID_FILE")" 2>/dev/null || true
    fi
    if [[ -n "$SERVER_WRAPPER_PID" ]]; then
        kill "$SERVER_WRAPPER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$(dirname "$REWARD_FILE")"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed or not in PATH." >&2
    echo 0 > "$REWARD_FILE"
    exit 1
fi

if [[ ! -d "$MICROBLOG_DIR" ]]; then
    echo "Error: MICROBLOG_DIR does not exist: $MICROBLOG_DIR" >&2
    echo 0 > "$REWARD_FILE"
    exit 1
fi

MICROBLOG_DIR="$(cd "$MICROBLOG_DIR" && pwd)"
export DATABASE_URL="${DATABASE_URL:-sqlite:///${TMP_DIR}/microblog_test.db}"

UV_APP_ARGS=(--directory "$MICROBLOG_DIR")
if [[ ! -f "$MICROBLOG_DIR/pyproject.toml" ]]; then
    UV_APP_ARGS+=(--no-project --python "$MICROBLOG_PYTHON")
    if [[ -f "$MICROBLOG_DIR/requirements.txt" ]]; then
        UV_APP_ARGS+=(--with-requirements "$MICROBLOG_DIR/requirements.txt")
    fi
fi

API_HOST="$API_HOST" API_PORT="$API_PORT" SERVER_PID_FILE="$SERVER_PID_FILE" \
    uv run "${UV_APP_ARGS[@]}" python -c "
import importlib, os, sys

with open(os.environ['SERVER_PID_FILE'], 'w') as f:
    f.write(str(os.getpid()))

candidates = [os.environ['APP_MODULE']] if os.environ.get('APP_MODULE') else [
    'microblog:app',
    'main:app',
    'app.main:app',
    'app:app',
]

app = None
last_err = None
for spec in candidates:
    mod_name, attr_name = spec.split(':', 1)
    try:
        mod = importlib.import_module(mod_name)
        candidate = getattr(mod, attr_name, None)
        if candidate is not None:
            app = candidate
            break
    except Exception as exc:
        last_err = exc

if app is None:
    print(f'Error: could not load application from {candidates}: {last_err}', file=sys.stderr)
    sys.exit(1)

host = os.environ['API_HOST']
port = int(os.environ['API_PORT'])

if hasattr(app, 'wsgi_app'):
    try:
        from app import db
        with app.app_context():
            db.create_all()
    except Exception:
        pass
    app.run(host=host, port=port, debug=False, use_reloader=False)
else:
    import uvicorn
    uvicorn.run(app, host=host, port=port)
" >"$SERVER_LOG" 2>&1 &
SERVER_WRAPPER_PID=$!

READY=0
for _ in $(seq 1 60); do
    if uv run --directory "$SCRIPT_DIR" python -c "
import os, sys, httpx
url = os.environ['API_BASE_URL'].rstrip('/') + '/api/users'
try:
    httpx.get(url, timeout=1.0)
except httpx.RequestError:
    sys.exit(1)
" >/dev/null 2>&1; then
        READY=1
        break
    fi
    if ! kill -0 "$SERVER_WRAPPER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

if [[ "$READY" -ne 1 ]]; then
    echo "Error: microblog server failed to start at $API_BASE_URL" >&2
    cat "$SERVER_LOG" >&2
    echo 0 > "$REWARD_FILE"
    exit 1
fi

if uv run --directory "$SCRIPT_DIR" pytest "$@"; then
    echo 1 > "$REWARD_FILE"
    exit 0
else
    status=$?
    echo 0 > "$REWARD_FILE"
    exit "$status"
fi
