#!/usr/bin/env bash
# ==============================================================================
# Certificate Manager - Native Desktop GUI Launcher untuk Linux
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================="
echo "🌟 Membuka Certificate Manager (Desktop GUI)..."
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
exec "$PYTHON_BIN" -m certslice gui "$@"
