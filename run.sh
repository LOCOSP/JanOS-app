#!/bin/bash
# JanOS — run from project .venv
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No .venv found — running setup..."
    ./setup.sh || exit 1
fi

# Default to /dev/ttyUSB0 if no device specified
if [ $# -eq 0 ] && [ -e /dev/ttyUSB0 ]; then
    exec .venv/bin/python3 -m janos /dev/ttyUSB0
else
    exec .venv/bin/python3 -m janos "$@"
fi
