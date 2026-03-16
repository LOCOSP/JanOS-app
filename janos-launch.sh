#!/bin/bash
cd "$(dirname "$0")"
exec lxterminal --title=JanOS --no-remote -e bash -c '.venv/bin/python3 -m janos; read -p "Press Enter..."'
