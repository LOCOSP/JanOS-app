#!/bin/bash
# JanOS — run from project .venv
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No .venv found — running setup..."
    ./setup.sh || exit 1
fi

# Python auto-detects ESP32 port (ttyUSB0-3, ttyACM0-3) if not specified
exec .venv/bin/python3 -m janos "$@"
