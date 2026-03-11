#!/bin/bash
# JanOS — setup virtual environment and install dependencies
set -e
cd "$(dirname "$0")"

echo "=== JanOS Setup ==="

if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv .venv
else
    echo "[*] Virtual environment already exists."
fi

echo "[*] Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "[OK] Setup complete!"
echo ""
echo "Run JanOS:"
echo "  ./run.sh /dev/ttyUSB0"
echo ""
echo "Or manually:"
echo "  .venv/bin/python3 -m janos /dev/ttyUSB0"
