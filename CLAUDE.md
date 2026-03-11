# CLAUDE.md — JanOS Project Context

> Shared context for all Claude Code instances working on this project.
> Update this file after every significant change.

## Project Overview

**JanOS** — Python TUI (urwid) for controlling ESP32-C5 WiFi security testing device.
Runs on ClockworkPi uConsole via SSH/serial. Version: **2.3.0**

## Repository Workflow

| Remote   | URL                                                         | Purpose           |
|----------|-------------------------------------------------------------|--------------------|
| `origin` | `gitlab.uni.opole.pl/loco/JanOS-app.git` (private, PAT)    | Primary dev repo   |
| `github` | `github.com/LOCOSP/JanOS-app` (public)                     | Public mirror      |

- **Every change**: commit + `git push origin main` (GitLab)
- **Publish release**: `git push github main` (manually, when ready)
- **App auto-update**: pulls from GitHub (`git pull github main`) — no auth needed
- **Dev deploy to uConsole**: pulls from GitLab (`git pull origin main`)

### Authentication
- GitLab PATs (read+write) configured in local `.git/config` on PC and uConsole
- GitHub: public, no auth needed for clone/pull
- **Never commit PATs or credentials to the repo**

## Deploy Target

- **Device**: ClockworkPi uConsole
- **User**: `locosp`
- **Work IP**: `10.59.40.57` | **Home IP**: `192.168.1.238`
- **Password**: `Cloude123!`
- **App path**: `~/python/JanOS-app/`
- **ESP32 serial**: `/dev/ttyUSB0`
- **Python**: `python3` (3.13.5, venv in PATH at `~/.pygpsclient/bin/python3`)
- **Deploy = SSH + `git pull`** on uConsole from GitLab (origin)

## User Preferences

- **No `Co-Authored-By`** in commits — never add it
- **Commit and push after every change** (to GitLab origin)
- **Windows**: use `python` not `python3`
- **Deploy after every change** for testing on uConsole
- Use `paramiko` for SSH (sshpass not available on Windows)

## Project Structure

```
JanOS-app/
├── janos/
│   ├── __init__.py          # __version__ = "2.3.0"
│   ├── __main__.py          # Entry point: python -m janos /dev/ttyUSB0
│   ├── config.py            # Constants, serial commands, URLs
│   ├── app_state.py         # Shared state object
│   ├── serial_manager.py    # ESP32 serial communication
│   ├── network_manager.py   # Network/AP data parsing
│   ├── loot_manager.py      # Loot capture & DB (loot_db.json)
│   ├── gps_manager.py       # GPS NMEA parsing (/dev/ttyAMA0)
│   ├── privacy.py           # Private mode (MAC/GPS obfuscation)
│   ├── hc22000.py           # HCCAPX → .22000 conversion
│   ├── updater.py           # Auto-update from GitHub
│   ├── aio_manager.py       # AIO v2 module status
│   ├── flash_manager.py     # ESP32 firmware flashing
│   └── tui/
│       ├── app.py           # JanOSTUI — main loop, overlays, serial dispatch
│       ├── header.py        # Top bar (version, device, uptime)
│       ├── footer.py        # Status bar (GPS, loot path)
│       ├── tabs.py          # Tab navigation (1-4)
│       ├── palette.py       # urwid color scheme
│       ├── screens/
│       │   ├── home.py      # Sidebar: logo, GPS, loot counters
│       │   ├── scan.py      # WiFi scan + network table
│       │   ├── sniffer.py   # Packet sniffer + AP/client tree
│       │   ├── attacks.py   # Attack modes (deauth, handshake, etc.)
│       │   ├── portal.py    # Captive portal management
│       │   ├── evil_twin.py # Evil twin attack
│       │   └── addons.py    # Extensions (BT scan, airtag, wardrive)
│       └── widgets/
│           ├── network_table.py     # AP table with RSSI colors
│           ├── data_table.py        # Generic data table
│           ├── confirm_dialog.py    # Yes/No overlay
│           ├── info_dialog.py       # OK-only overlay
│           ├── text_input_dialog.py # Text input overlay
│           ├── file_picker.py       # File selection overlay
│           ├── choice_dialog.py     # Multiple choice overlay
│           ├── log_viewer.py        # Scrollable log display
│           ├── startup_screen.py    # Startup checks + countdown
│           └── creature.py          # ASCII art animation
├── requirements.txt
├── README.md
├── CLAUDE.md                # This file (GitLab only)
└── .gitignore
```

## Key Architecture

### Serial Protocol
- ESP32 communicates via USB serial at 115200 baud
- Commands: plain text strings (`scan_networks`, `start_sniffer`, `stop`, etc.)
- Responses: text lines, parsed with regex in each screen's `handle_serial_line()`
- Binary data (PCAP/HCCAPX): base64-encoded in `DOWNLOAD:` lines

### Loot System
- Session dir: `loot/YYYY-MM-DD_HH-MM-SS/`
- Sub-dirs: `handshakes/`, `evil_twin/`, `portals/`, `passwords/`
- `serial.log` — raw serial transcript
- `loot_db.json` — cumulative stats across all sessions
- Counters: S(sessions), PCAP, HCCAPX, 22K, PWD, ET

### HC22000 Conversion
- `hc22000.py`: parses HCCAPX binary (393-byte records), validates completeness
- Complete = valid signature + message_pair in range + non-zero MIC/ANonce + ESSID present
- Output: `WPA*02*MIC*MAC_AP*MAC_STA*ESSID_HEX*ANONCE*EAPOL*MP`
- GPS coordinates embedded as comments when available
- Retroactive: `_rebuild_db()` generates .22000 for existing .hccapx files

### Auto-Update Flow
1. Background thread checks `APP_UPDATE_URL` (GitHub raw) for remote `__version__`
2. If newer → shows `ConfirmDialog` after startup screen dismisses
3. On Yes → `git stash --quiet` → `git pull github main` → shows result
4. `_ensure_github_remote()` auto-adds `github` remote if missing

### GPS
- UART: `/dev/ttyAMA0` at 9600 baud
- Privacy mode: ±0.01° (~1.1km) random noise on coordinates
- GPS fix embedded in PCAP/HCCAPX/22000 filenames and comments

### Keyboard Shortcuts
- `1-4`: Switch tabs | `Tab/→`: Next tab | `Shift+Tab/←`: Prev tab
- `P`: Toggle private mode | `M`: Toggle mobile mode (hide sidebar)
- `q`: Quit (with confirmation) | `9`: Stop all operations

## Firmware Companion

- Repo: `LOCOSP/projectZero` (GitHub, public)
- Chip: ESP32-C5
- Flash: `esptool.py` at 460800 baud, offsets: bootloader@0x2000, partition@0x8000, app@0x20000
- Release API: `api.github.com/repos/LOCOSP/projectZero/releases/latest`

## Recent Changes Log

| Date       | Version | Change                                                    |
|------------|---------|-----------------------------------------------------------|
| 2025-03-10 | 2.3.0   | HC22000 auto-generation from HCCAPX                       |
| 2025-03-10 | 2.3.0   | Auto-update check on startup (GitHub)                     |
| 2025-03-10 | 2.3.0   | GitLab migration (primary), GitHub mirror                 |
| 2025-03-10 | 2.3.0   | Loot dashboard legend in README                           |
| 2025-03-09 | 2.2.0   | Portal file picker, sample portal embedded in code        |
| 2025-03-09 | 2.2.0   | GPS privacy mode, startup checks dialog                   |
