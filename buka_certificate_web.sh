#!/usr/bin/env bash
# ==============================================================================
# Certificate Manager - Web Edition Launcher untuk Linux
# Port: 8500 (Sesuai alokasi Antigravity Account 6)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================="
echo "🌟 Membuka Certificate Manager - Precision Studio v3.0..."
echo "======================================================="

# Pastikan environment Python Linux tersedia
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "❌ Error: Python tidak ditemukan!"
    exit 1
fi

echo "✓ Menggunakan Python: $PYTHON_BIN"
echo "✓ Membuka antarmuka Web Studio di browser (http://127.0.0.1:8500)..."

exec "$PYTHON_BIN" -m certslice web --port 8500 "$@"
