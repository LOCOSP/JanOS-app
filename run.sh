#!/bin/bash
# JanOS — run from project .venv
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No .venv found — running setup..."
    ./setup.sh || exit 1
fi

# Root required for: scapy (MITM, Dragon Drain), airmon-ng, tcpdump, IP forwarding
# Python auto-detects ESP32 port (ttyUSB0-3, ttyACM0-3) if not specified
exec sudo .venv/bin/python3 -m janos "$@"
