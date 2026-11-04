#!/usr/bin/env bash
# ==============================================================================
# Certificate Manager - Application Launcher
# Starts background server if needed and launches standalone app window.
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p "$SCRIPT_DIR/out"
export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH}"

PORT=8500
URL="http://127.0.0.1:$PORT"

# Check if server is already running
if ! curl -s --max-time 1 "$URL/api/status" >/dev/null 2>&1; then
    # Start server in background
    if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
        nohup "$SCRIPT_DIR/.venv/bin/python" -m certslice web --port "$PORT" --no-browser > "$SCRIPT_DIR/out/web_app.log" 2>&1 &
    else
        nohup python3 -m certslice web --port "$PORT" --no-browser > "$SCRIPT_DIR/out/web_app.log" 2>&1 &
    fi

    # Wait up to 5 seconds for server to be ready
    for i in {1..20}; do
        if curl -s --max-time 1 "$URL/api/status" >/dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done
fi

# Launch in App Mode (Brave PWA app window), or fallback to default browser
if command -v brave >/dev/null 2>&1; then
    exec brave --app="$URL" --name="CertificateManager" --class="CertificateManager" "$@"
elif command -v google-chrome >/dev/null 2>&1; then
    exec google-chrome --app="$URL" --name="CertificateManager" --class="CertificateManager" "$@"
elif command -v chromium >/dev/null 2>&1; then
    exec chromium --app="$URL" --name="CertificateManager" --class="CertificateManager" "$@"
elif command -v firefox >/dev/null 2>&1; then
    exec firefox --new-window "$URL" "$@"
else
    exec xdg-open "$URL"
fi
