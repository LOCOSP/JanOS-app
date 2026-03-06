#!/bin/bash
cd /home/locosp/python/JanOS-app
exec lxterminal --title=JanOS --no-remote -e bash -c 'python3 -m janos /dev/ttyUSB0; read -p "Press Enter..."'
