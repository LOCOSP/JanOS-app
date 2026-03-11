#!/bin/bash
# JanOS — run from project .venv
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No .venv found — running setup..."
    ./setup.sh || exit 1
fi

exec .venv/bin/python3 -m janos "$@"
