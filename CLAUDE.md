# CLAUDE.md — JanOS Project Context

> Shared context for all Claude Code instances working on this project.
> Update this file after every significant change.

## Project Overview

**JanOS** — Python TUI (urwid) for controlling ESP32-C5 WiFi security testing device.
Runs on ClockworkPi uConsole via SSH/serial. Version: **2.5.3**

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

### VPN (home deploy)
- GitLab (`gitlab.uni.opole.pl`) is only reachable from the university network
- **From home**: connect VPN first on uConsole before `git pull origin main`
- **VPN tool**: `openfortivpn` (v1.23.1) with config in `/etc/openfortivpn/config`
- **Connect**: `sudo ~/vpn-connect.sh` (start/stop/status)
- **Gateway**: `remote.uni.opole.pl:443`, realm `cnt`, user `loco@uni.opole.pl`
- VPN assigns IP from `192.168.201.x` range, routes university subnets via `ppp0`
- After VPN is up, `git pull origin main` works normally

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
│   ├── __init__.py          # __version__ = "2.5.0"
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
│   ├── aio_manager.py       # AIO v2 module status (direct GPIO via pinctrl)
│   ├── lora_manager.py      # LoRa SX1262 SPI control (sniffer, scanner, tracker, meshcore, meshtastic)
│   ├── flash_manager.py     # ESP32 firmware flashing (projectZerobyLOCOSP assets)
│   ├── upload_manager.py    # WiGLE + WPA-sec cloud upload
│   └── tui/
│       ├── app.py           # JanOSTUI — main loop, overlays, serial dispatch
│       ├── header.py        # Top bar (version, device, uptime, CPU, RAM, battery)
│       ├── footer.py        # Status bar (GPS, loot path)
│       ├── tabs.py          # Tab navigation (1-5)
│       ├── palette.py       # urwid color scheme
│       ├── screens/
│       │   ├── home.py      # Sidebar: logo, GPS, loot counters, MC stats
│       │   ├── scan.py      # WiFi scan + network table
│       │   ├── sniffers.py  # Sniffers menu (WiFi WD + BT WD + Packet Sniffer)
│       │   ├── wardriving.py# WiFi Wardriving screen (continuous WiFi scan + GPS)
│       │   ├── bt_wardriving.py # BT Wardriving screen (continuous BLE scan + GPS)
│       │   ├── sniffer.py   # Packet sniffer + AP/client tree
│       │   ├── attacks.py   # Attack modes (deauth, handshake, WPA-sec upload)
│       │   ├── portal.py    # Captive portal management
│       │   ├── evil_twin.py # Evil twin attack
│       │   ├── dragon_drain.py # Dragon Drain — WPA3 SAE flood (Python/scapy)
│       │   ├── mitm.py      # MITM — ARP spoofing attack (Python/scapy)
│       │   ├── bt_ducky.py  # BlueDucky — Classic BT HID injection (CVE-2023-45866)
│       │   ├── race_attack.py # RACE — Airoha headphone jacking (CVE-2025-20700/20701/20702)
│       │   ├── addons.py    # Extensions (flash, AIO, LoRa sniffer/scanner/tracker/meshcore/meshtastic)
│       │   └── map_screen.py# Map tab — braille world map with GPS loot points
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
│           ├── creature.py          # ASCII art animation
│           ├── braille_map.py       # Braille-character world map widget
│           └── coastline.py         # Simplified world coastline coordinates
├── requirements.txt
├── setup.sh                 # Create .venv + install deps
├── run.sh                   # Run JanOS from .venv: ./run.sh /dev/ttyUSB0
├── janos-launch.sh          # Desktop launcher (lxterminal + .venv)
├── janos-launcher           # Alternative desktop launcher
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
- `meshcore_nodes.csv` — CSV: timestamp,node_id,type,name,lat,lon,rssi,snr (dedup by node_id)
- `meshcore_messages.log` — `[HH:MM:SS] [channel] message (RSSI:x)`
- `loot_db.json` — cumulative stats across all sessions
- `wardriving.csv` — WiGLE-format CSV: MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,Lat,Lon,Alt,Accuracy,Type (dedup by BSSID)
- Counters: S(sessions), PCAP, HCCAPX, 22K, PWD, ET, mc_nodes, mc_messages, wardriving

### HC22000 Conversion
- `hc22000.py`: parses HCCAPX binary (393-byte records), validates completeness
- Complete = valid signature + message_pair in range + non-zero MIC/ANonce + ESSID present
- Output: `WPA*02*MIC*MAC_AP*MAC_STA*ESSID_HEX*ANONCE*EAPOL*MP`
- GPS coordinates embedded as comments when available
- Retroactive: `_rebuild_db()` generates .22000 for existing .hccapx files

### Auto-Update Flow (App + Firmware)
Background thread at startup checks both (non-blocking, silent on error):
1. **App version**: `APP_UPDATE_URL` (GitHub raw `__init__.py`) → `ConfirmDialog` → `git pull github main`
2. **After pull**: runs `setup.sh` (pip install + Pi 5 lgpio fix), falls back to pip on Windows
3. **Firmware version**: `FIRMWARE_RELEASE_URL` (GitHub releases API) → `InfoDialog` with hint to Add-ons tab 4
4. Dialogs chained: app update first → firmware update second (if both available)
5. Local firmware version saved in `~/.janos_fw_version` after each flash
6. `_ensure_github_remote()` auto-adds `github` remote if missing
7. `_find_project_root()` walks up from `janos/` to find `.git/` directory

### Firmware Version Detection
- **Saved file**: `~/.janos_fw_version` loaded at startup → `state.firmware_version` (uses `SUDO_USER` to find real home under sudo)
- **Serial boot banner**: `=== APP_MAIN START (v1.6.0) ===` parsed from serial
- **ESP-IDF log**: `JanOS version: 1.6.0` parsed from serial
- **Priority**: saved file at init → overwritten by serial detection if seen
- **Sidebar**: shows `Firmware vX.Y.Z` under device connection status

### GPS
- UART: `/dev/ttyAMA0` at 9600 baud
- Privacy mode: ±0.01° (~1.1km) random noise on coordinates
- GPS fix embedded in PCAP/HCCAPX/22000 filenames and comments

### LoRa SX1262 Integration
- **Hardware**: SX1262 on AIO v2 board, SPI `/dev/spidev1.0`
- **Pins**: IRQ=GPIO26, Busy=GPIO24, Reset=GPIO25, DIO2_AS_RF_SWITCH, DIO3_TCXO_VOLTAGE
- **Library**: `LoRaRF` (PyPI) — direct SPI communication, no meshtasticd
- **GPIO power**: LORA=GPIO16 (on/off via `pinctrl set 16 op dh|dl`)
- **EU868 frequencies**: 868.1, 868.3, 868.5, 867.1, 867.3, 867.5, 867.7, 867.9 MHz
- **APRS 433 frequencies**: 433.775 (SF12/CR5), 434.855 (SF9/CR7), 439.9125 (SF12/CR5)
- **APRS packet format**: 3-byte prefix `\x3c\xff\x01` + `CALL>DEST:=DDMM.MMN/DDDMM.MMEO .../A=AAAAAA`
- **APRS ref**: SQ2CPA/LoRa_APRS_Balloon (GitHub)
- **Features** (in Add-ons tab, visible only when LORA ON):
  - `[6]` LoRa Sniffer — single freq/SF listener, detects encrypted vs printable
  - `[9]` MeshCore Sniffer — 869.618 MHz EU/UK Narrow, decodes headers+adverts+public chat (AES-128)
  - `[0]` Meshtastic Sniffer — 869.525 MHz Medium Fast SF11 BW250k
  - `[7]` LoRa Scanner — cycles EU868 + APRS 433 freqs × SFs (7-12)
  - `[8]` Balloon Tracker — cycles APRS 433 + UKHAS 868 profiles, parses APRS position/alt and UKHAS CSV
- **Architecture**: background thread with queue (same as FlashManager)
- **RX mode**: RX_SINGLE for sniffer/scanner/tracker; RX_CONTINUOUS with SPI IRQ polling for MeshCore/Meshtastic
- **Auto-stop**: LoRa operations stop when LORA GPIO toggled OFF
- **Direct sniffer switching**: pressing a different sniffer key while one is running stops old + starts new in one action (no double-press needed)
- **Radio cleanup**: `_cleanup_radio()` uses `spi.close()` directly instead of `lora.end()` to preserve GPIO BCM pin mode (LoRaRF's `gpio.cleanup()` clears `setmode` which only runs at module import)
- **Thread safety**: `stop()` calls `thread.join(timeout=5)` to ensure old thread finishes before new sniffer starts
- **MeshCore protocol**: sync_word=0x1424, preamble=16, public PSK=8b3387e9c5cdea6ac9e5edbaa115cd72 (AES-128-ECB)
- **MeshCore packet header**: 0bVVPPPPRR (Version, PayloadType, RouteType), path with hop hashes
- **MeshCore public channel hash**: 0x11, group text decryptable with known PSK
- **MeshCore adverts (type 0x04)**: plaintext -- node ID (Ed25519), name, type, GPS
- **MeshCore loot callbacks**: `_on_node(node_id, type, name, lat, lon, rssi, snr)` and `_on_message(channel, message, rssi)` — set by addons.py, call loot_manager save methods
- **Meshtastic Medium Fast**: 869.525 MHz, SF11, BW250k, CR8
- **Sidebar**: shows `LoRa Packets: N` when LORA ON, `MC  Nodes:X │ Msgs:Y` for MeshCore loot, `MC:nodes/msgs` in all-time totals

### Wardriving + Cloud Upload
- **Sniffers tab** (was "Sniffer"): menu with [1] Wardriving WiFi, [2] Wardriving BT, [3] Packet Sniffer
- **WiFi Wardriving**: continuous WiFi scan with GPS geo-tagging, dedup by BSSID (strongest RSSI)
- **BT Wardriving**: continuous BLE scan with GPS geo-tagging, dedup by MAC (strongest RSSI)
  - Uses ESP32 `bt_scan` command, cycles every ~12-15s with timeout-based scan completion (15s auto-finish)
  - BT device regex: `r'^\s*\d+\.\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)\s*dBm(?:\s+Name:\s*(.+?))?'`
  - Same GPS flow as WiFi wardriving (GPS fix check, wait dialog)
  - Creature: `bt_scan` animation when BT wardriving active
- **WiGLE CSV format**: data saved directly in WiGLE-compatible format (pre-header + header + data)
  - Same `wardriving.csv` file stores both WiFi (Type=WIFI) and BLE (Type=BLE) entries
  - Type column (index 10) differentiates WiFi vs BT entries
  - `save_wardriving_bt()` in loot_manager: saves BLE device with Type=BLE, AuthMode=[BLE]
- **AuthMode mapping**: ESP32 auth (WPA2) → WiGLE format ([WPA2-PSK-CCMP][ESS])
- **Accuracy**: GPS HDOP × 5.0 meters
- **WiGLE upload** (`[w]` in wardriving/bt_wardriving): POST multipart to `api.wigle.net/api/v2/file/upload` with Basic Auth
- **WiGLE user stats**: `fetch_wigle_user_stats()` — GET `api.wigle.net/api/v2/stats/user`, returns discoveredWiFiGPS, discoveredBtGPS, rank
  - Background thread fetch, cached 1h (`_wigle_stats_ts`), shown in sidebar when token configured
- **WPA-sec upload** (`[u]` in attacks): POST .pcap files to `wpa-sec.stanev.org/?submit` with cookie auth
- **Config**: `WIGLE_API_NAME/TOKEN`, `WPASEC_KEY` in config.py (env override: `JANOS_WIGLE_NAME/TOKEN`, `JANOS_WPASEC_KEY`)
- **upload_manager.py**: `upload_wigle()`, `upload_wpasec()`, `upload_wpasec_all()`, `find_wardriving_csvs()`, `fetch_wigle_user_stats()`
- **Background upload**: threading with result polled via `_upload_result` in `refresh()`
- **Creature**: wardriving animation (`>*>` waves, success attr), `bt_scan` for BT wardriving
- **GPS checks**: wardriving requires GPS fix; dialog to wait or cancel if no fix

### Map Tab (Braille World Map)
- **Tab 5**: vector world map rendered with Unicode braille characters (U+2800-U+28FF)
- **Resolution**: 2×4 dots per character cell → 160×80 "pixels" on 80×20 char area
- **Projection**: equirectangular (lat/lon → pixel)
- **Coastline**: Natural Earth 50m data, RDP-simplified to ~2250 points in 125 segments
- **GPS points**: collects from ALL loot sessions — wardriving.csv, handshakes/*.gps.json, bt_devices.csv, meshcore_nodes.csv
- **Point types**: HS (red), WiFi (green), BT (cyan), MC (yellow) — toggle with `[h/w/b/m]`
- **Navigation**: arrow keys to pan, `+`/`-` to zoom in/out, `[c]` center on points, `[0]` reset view
- **Viewport**: zoom level 0 uses (360°, 155°) lat span (cropped poles), center_lat=12.5° to fill screen
- **Keys**: `[r]` refresh, `[h]` handshakes, `[w]` WiFi, `[b]` BT, `[m]` MeshCore, arrows/+/-/c/0 navigation
- **Caching**: `get_gps_points()` cached 30s to avoid FS scan on every refresh
- **No external deps**: custom `BrailleCanvas` class (no drawille needed)
- **Files**: `braille_map.py` (widget), `coastline.py` (data), `map_screen.py` (screen), `get_gps_points()` in loot_manager

## Firmware Companion

- Repo: `LOCOSP/projectZero` (GitHub, public fork of `C5Lab/projectZero`)
- Upstream: `C5Lab/projectZero` — periodically merge from `development` branch
- Chip: ESP32-C5
- Firmware version: **1.7.0** (v1.6.0-pre-hid tag for rollback)
- Binary name: `projectZerobyLOCOSP.bin` (CMake project = `projectZerobyLOCOSP`)
- Release naming: `projectZerobyLOCOSP X.Y.Z`, assets: `projectZerobyLOCOSP-X.Y.Z.zip`
- Flash: `esptool.py` at 460800 baud, offsets: bootloader@0x2000, partition@0x8000, projectZerobyLOCOSP.bin@0x20000
- Release API: `api.github.com/repos/LOCOSP/projectZero/releases/latest`
- OTA owner: `LOCOSP` (changed from C5Lab in v1.6.0)
- Local repo: `C:\Users\Borys\PRYWATNE\Python\JanOS\projectZero`
- Remotes: `origin` → LOCOSP/projectZero (GitHub), `upstream` → C5Lab/projectZero

### Firmware Features (merged into main)

**Our custom (LOCOSP-only):**
- `start_handshake_serial` — SD-less PCAP/HCCAPX capture via serial (base64)
- Chunked base64 HTML transfer: `set_html_begin` / `set_html <chunk>` / `set_html_end`
- PSRAM buffer (1MB) for large portal pages
- SSID with spaces in `start_portal`
- Console `max_cmdline_length` = 1024 (was 100)
- **BLE HID keyboard injection** (v1.7.0): `bt_hid` + `bt_hid_type <text>` commands
  - NimBLE HOG profile (HID over GATT): HID Service 0x1812, Battery 0x180F, DevInfo 0x180A
  - Just Works pairing (NoInputNoOutput), advertises as "BLE Keyboard"
  - OLED: advertising status, connected peer MAC, ready indicator
  - Works on Android 8+, iOS, Windows 10+, macOS (requires pairing acceptance)

**From upstream C5Lab (merged 2026-03-11):**
- Multi-display OLED support: SSD1306, SH1107, SH1106, M5 Unit LCD (auto-detect via I2C)
- New files: `oled_display.c` / `oled_display.h` (LVGL 9.2.0 dependency)
- WiGLE cloud upload: `wigle_key set/read`, `wigle_upload`
- External GPS feeds: `GPS_MODULE_EXTERNAL`, `GPS_MODULE_EXTERNAL_CAP`
- New commands: `set_gps_position`, `display set/read`
- Attack visualization on OLED (real-time stats)
- Password deduplication in Evil Twin
- Detailed attack info display on OLED

### Firmware Serial Protocol (for TUI integration)

**Handshake serial output format:**
```
--- PCAP BEGIN ---
<base64 lines>
--- PCAP END ---
PCAP_SIZE: <bytes>
--- HCCAPX BEGIN ---
<base64 lines>
--- HCCAPX END ---
SSID: <name>  AP: <XX:XX:XX:XX:XX:XX>
```

**HTML transfer protocol:**
```
set_html_begin              → allocate 1MB PSRAM buffer
set_html <b64_chunk>        → append chunk (repeat)
set_html_end                → decode base64 → activate portal HTML
```

**New commands available (not yet in TUI):**
- `wigle_key set <name> <token>` / `wigle_key read` / `wigle_upload` (firmware-side; app uses own HTTP upload)
- `set_gps_position <lat> <lon> [alt] [acc]` / `set_gps_position_cap`
- `display set {sh1107|ssd1306|auto}` / `display read`
- `beacon_spam_random` / `beacon_spam_rickroll` / `beacon_spam_list <file>`
- `karma` / `karma_off`
- `bt_scan` / `bt_track <MAC>` / `bt_airtag_scan` (BT commands from firmware)

### Bluetooth Features (TUI)
- **BLE Scan** (`b` key): discover BLE devices, count saved to loot
- **BT Tracker** (`t` key): track specific BLE MAC address (asks for MAC via dialog)
- **AirTag Scanner** (`a` key): detect Apple AirTags + Samsung SmartTags
- **BT Loot**: `bt_devices.csv` and `bt_airtag.log` with GPS geo-tagging
- **AppState fields**: `bt_scan_running`, `bt_tracking_running`, `bt_tracking_mac`, `bt_airtag_running`, `bt_devices`, `bt_airtags`, `bt_smarttags`, `bt_wardriving_running`, `bt_wardriving_devices`
- **Sidebar**: BT loot line + all-time BT totals split into separate line
  - Wardriving: `WD WiFi:X | BT:Y` (split counters from wardriving.csv Type column)
  - WiGLE stats: `WiGLE: WiFi:X BT:Y Rank:#N` (when token configured, fetched every 1h)
- **Creature animations**: `bt_scan` ())) BLE waves), `bt_tracking` (>>> hunting), `bt_airtag` ([*] tags), `lora` (~=~ radio)

### Advanced Attacks (Python-native, no ESP32)
- **Dragon Drain** (`d` key): WPA3 SAE Commit flood DoS (CVE-2019-9494)
  - Sends spoofed 802.11 Authentication frames with SAE algo=3, random Scalar (32B) + Element (64B) on NIST P-256
  - Forces AP to perform expensive ECC computation (~16 frames/sec, randomized source MACs)
  - **Auto monitor mode**: detects managed WiFi adapters, runs `airmon-ng start` automatically; polls for adapter if none plugged in
  - **WPA3 AP scan**: 10s beacon sniff on monitor interface, parses RSN IE for SAE AKM (00:0F:AC:08), shows FilePicker with SSID/BSSID/CH/RSSI; manual BSSID input as fallback
  - Uses scapy: `RadioTap() / Dot11(type=0, subtype=11) / Dot11Auth(algo=3, seqnum=1, status=0) / Raw(payload)`
- **MITM** (`m` key): ARP spoofing man-in-the-middle
  - Poisons ARP caches between victim(s) and gateway
  - 3 target modes: single IP, subnet scan + select, all devices
  - Live parsing: DNS queries, HTTP requests, cleartext credentials (FTP/Telnet/POP3/IMAP)
  - Full pcap capture via tcpdump → `loot/<session>/mitm/capture_<ts>.pcap`
  - Enables IP forwarding on start, restores ARP tables + IP forwarding on stop
  - Requires network adapter connected to target network (managed mode)
  - **Pcap viewer** `[l]`: browse and inspect pcaps from ALL loot sessions, scapy-based packet table (500 pkt limit)
  - Sidebar: `MITM:N` counter in current session and all-time totals
- **Hardware detection dialogs**: Dragon Drain waits for monitor mode interface (polls every 2s, auto-airmon), MITM waits for network interface with IP
- **Monitor mode cleanup on quit**: `_quit()` runs `airmon-ng stop` on all monitor interfaces before exit
- **ESP32 detection dialog**: ESP32-dependent attacks (WiFi 1-7, BT b/t/a) show waiting dialog that polls for ESP32 connection every 2s, auto-proceeds when connected
- **ESP32 optional**: app starts without device argument (`./run.sh`), `__main__.py` device defaults to `""`
- **BlueDucky** (`k` key): Classic BT HID keystroke injection (CVE-2023-45866)
  - Exploits unauthenticated L2CAP HID pairing on unpatched devices
  - Works on: Android <Dec 2023, macOS/iOS <Dec 2023, unpatched Linux/Windows
  - Uses uConsole's built-in Bluetooth adapter (pybluez + D-Bus)
  - DuckyScript parser: STRING, DELAY, GUI, CTRL, ALT, ENTER, Fn keys
  - Built-in payloads: Rick Roll, Hello Test, custom from `~/payloads/*.txt`
  - Rick Roll auto-flow `[r]`: scan → pick target → connect → execute
  - L2CAP PSM 17 (control) + PSM 19 (interrupt) for HID reports
  - `[s]` scan, `[c]` connect (MAC input), `[1-9]` quick-select, `[p]` payload picker
  - **Dependencies**: `pybluez>=0.23`, `dbus-python>=1.3.2` (system site-packages)
- **RACE Attack** (`j` key): Airoha headphone jacking (CVE-2025-20700/20701/20702)
  - Exploits unauthenticated RACE debug protocol in Airoha BT chips
  - Affected: Sony WH/WF series, JBL, Bose QC, Marshall, Jabra, Xiaomi
  - Attack chain: BLE scan → connect (no pairing) → extract link keys → impersonate → capture audio
  - **RACE protocol**: GATT service with TX/RX characteristics, 6-byte header (head+type+length+cmd_id)
  - **GATT UUIDs**: Airoha `5052494D-2DAB-0341-...`, Sony `dc405470-a351-...`, TRSPX `49535343-FE7D-...`
  - **Commands**: GetLinkKey (0x0CC0), GetBDAddress (0x0CD5), ReadFlashPage (0x0403)
  - **Impersonation**: bdaddr MAC spoof → inject link key to BlueZ `/var/lib/bluetooth/` → A2DP sink
  - **Audio capture**: parecord from bluez PulseAudio source → WAV file in loot
  - `[s]` scan, `[c]` device picker, `[1-9]` quick-select, `[e]` extract keys, `[h]` hijack, `[l]` listen
  - **Dependencies**: `bleak>=2.0.0` (BLE GATT), `bdaddr` (MAC spoofing), `parecord` (audio)
- **AppState fields**: `dragon_drain_running`, `dragon_drain_frames`, `mitm_running`, `mitm_packets`, `bt_ducky_running`, `race_running`
- **Creature animations**: `dragon_drain` (≋⚡≋ fire), `mitm` (←·→ intercept)
- **Dependencies**: `scapy>=2.5.0`, `pybluez>=0.23`, `dbus-python>=1.3.2`, `bleak>=2.0.0`

### Keyboard Shortcuts
- `1-5`: Switch tabs (Scan, Sniffers, Attacks, Add-ons, Map) | `Tab/→`: Next tab | `Shift+Tab/←`: Prev tab
- `P`: Toggle private mode | `M`: Toggle mobile mode (hide sidebar)
- `d`: Dragon Drain | `m`: MITM | `k`: BlueDucky | `j`: RACE — in Attacks tab
- `w`: WiGLE upload (wardriving) | `u`: WPA-sec upload (attacks)
- `q`: Quit (with confirmation, restores WiFi from monitor mode) | `9`: Stop all operations

## Recent Changes Log

| Date       | Version | Change                                                    |
|------------|---------|-----------------------------------------------------------|
| 2026-03-20 | 2.5.3   | Removed Watch Dogs game overlay (moved to standalone esp32-watch-dogs project) |
| 2026-03-20 | 2.5.3   | RACE: guided flow — each step tells you what to do next    |
| 2026-03-20 | 2.5.3   | Fix: stale game cmd file causing ESP32 stop loop           |
| 2026-03-17 | 2.5.3   | RACE Attack [j]: Airoha headphone jacking (CVE-2025-20700/20701/20702) |
| 2026-03-17 | 2.5.3   | RACE: BLE scan, RACE protocol, link key extraction, MAC spoof, audio capture |
| 2026-03-17 | 2.5.3   | BlueDucky [k]: Classic BT HID injection (CVE-2023-45866, pybluez+D-Bus) |
| 2026-03-17 | 2.5.3   | BlueDucky: Rick Roll auto-flow (scan→pick→connect→play)   |
| 2026-03-17 | 2.5.3   | Startup: bleak, pybluez, bdaddr, parecord checks + auto-install |
| 2026-03-17 | FW 1.7.0| ESP32-C5: BLE HID keyboard injection (NimBLE HOG profile)  |
| 2026-03-16 | 2.5.0   | MITM pcap viewer [l]: browse all sessions, scapy packet table, Private Mode |
| 2026-03-16 | 2.5.0   | MITM pcap counter in sidebar (current session + all-time totals)          |
| 2026-03-16 | 2.5.0   | Private Mode masking for Dragon Drain (BSSID, SSID) and MITM (IP, MAC, DNS, HTTP, creds) |
| 2026-03-16 | 2.5.0   | Auto-install tcpdump + aircrack-ng at startup and in setup.sh             |
| 2026-03-16 | 2.5.0   | Fix firmware version detection under sudo (SUDO_USER home resolution)     |
| 2026-03-16 | 2.5.0   | Run as root by default (sudo in run.sh, janos-launch.sh, janos-launcher)  |
| 2026-03-16 | 2.5.0   | Dragon Drain: WPA3 AP scan (10s beacon sniff + FilePicker) before attack |
| 2026-03-16 | 2.5.0   | Quit: airmon-ng stop on all monitor interfaces before exit            |
| 2026-03-16 | 2.5.0   | Dragon Drain: auto-detect managed WiFi + airmon-ng start automatically |
| 2026-03-16 | 2.5.0   | Hardware detection dialogs: WiFi adapter wait, ESP32 wait, auto-poll  |
| 2026-03-16 | 2.5.0   | Dragon Drain: WPA3 SAE Commit flood DoS (CVE-2019-9494, scapy)       |
| 2026-03-16 | 2.5.0   | MITM: ARP spoofing with live DNS/HTTP/credential capture + pcap       |
| 2026-03-16 | 2.5.0   | ESP32 optional: app starts without device, Advanced attacks only      |
| 2026-03-16 | 2.5.0   | Startup screen: scapy check, monitor mode detection, ESP32 info-level |
| 2026-03-16 | 2.5.0   | Attacks menu: Advanced section [d]DragonDrain [m]MITM                 |
| 2026-03-16 | 2.5.0   | Creature animations: dragon_drain (fire), mitm (intercept)            |
| 2026-03-13 | 2.4.5   | README: comprehensive rewrite — config section, Map tab, BT wardriving |
| 2026-03-13 | 2.4.5   | WiGLE stats refresh interval 5min → 1h                       |
| 2026-03-13 | 2.4.5   | Sidebar: WD WiFi/BT split counters + WiGLE user stats (1h cache) |
| 2026-03-13 | 2.4.5   | BT Wardriving: continuous BLE scan + GPS, same WiGLE CSV (Type=BLE) |
| 2026-03-13 | 2.4.5   | Sniffers tab: [1] WiFi WD, [2] BT WD, [3] Packet Sniffer    |
| 2026-03-13 | 2.4.5   | Map: pan/zoom navigation (arrows, +/-, center, reset), viewport crop (155° lat) |
| 2026-03-13 | 2.4.5   | Map tab: braille world map with GPS loot points (tab 5)     |
| 2026-03-13 | 2.4.5   | WPA-sec upload moved to Attacks screen ([u] key)            |
| 2026-03-13 | 2.4.5   | WiGLE + WPA-sec upload support, wardriving CSV in WiGLE format |
| 2026-03-13 | 2.4.5   | GPS sidebar: shows OFF when AIO GPS disabled, satellite Vis fallback |
| 2026-03-12 | FW 1.6.0| Firmware: rename to projectZerobyLOCOSP, OTA owner=LOCOSP, bump 1.5.5→1.6.0 |
| 2026-03-12 | 2.4.5   | Flash: search for projectZerobyLOCOSP assets in releases   |
| 2026-03-12 | 2.4.5   | config.py: FLASH_OFFSETS updated for projectZerobyLOCOSP.bin |
| 2026-03-12 | 2.4.5   | Creature animations: BT scan/tracking/airtag + LoRa frames, idle nudges |
| 2026-03-12 | 2.4.5   | Battery status in header bar (percent + voltage, right-aligned) |
| 2026-03-12 | 2.4.5   | SmartTags counter in BT loot sidebar and all-time totals   |
| 2026-03-12 | 2.4.5   | All-time totals split into WiFi / BT / LoRa lines          |
| 2026-03-12 | 2.4.5   | GPS geo-tagging for BT loot (devices + airtags CSVs)       |
| 2026-03-12 | 2.4.5   | Bluetooth attacks: BLE Scan, BT Tracker, AirTag Scanner + BT loot system |
| 2026-03-12 | 2.4.5   | MeshCore loot: nodes→CSV, messages→log, sidebar MC stats, loot_db totals |
| 2026-03-12 | 2.4.5   | Direct sniffer switching (press different key while running = auto-switch) |
| 2026-03-12 | 2.4.5   | GPIO cleanup fix: _cleanup_radio() bypasses lora.end() to preserve BCM pin mode |
| 2026-03-12 | 2.4.5   | stop() thread.join(timeout=5) for safe radio handoff between sniffers |
| 2026-03-12 | 2.4.0   | LoRa robustness: per-iteration error recovery, radio auto-reinit after 10 errors |
| 2026-03-12 | 2.4.0   | Encrypted LoRaWAN detection: binary packets show [Encrypted] hex, no garbled ASCII |
| 2026-03-12 | 2.4.0   | MeshCore sniffer [9]: EU/UK Narrow 869.618MHz, sync word 0x1424, preamble 16 |
| 2026-03-12 | 2.4.0   | MeshCore packet decoder: header parsing, Advertisement (plaintext), Group Text (AES-128 public PSK) |
| 2026-03-12 | 2.4.0   | Meshtastic sniffer [0]: Medium Fast 869.525MHz SF11 BW250k CR8 |
| 2026-03-12 | 2.4.0   | Sniffer CR parameter support (was hardcoded to 5), sync word + preamble config |
| 2026-03-12 | 2.4.0   | MeshCore RX_CONTINUOUS: direct SPI IRQ polling (bypass unreliable GPIO callback) |
| 2026-03-12 | 2.4.0   | MeshCore dedup: retransmissions filtered by header+payload hash (30s window) |
| 2026-03-12 | 2.4.0   | setLoRaPacket argument order fix (preamble was set to 0 instead of 16) |
| 2026-03-12 | 2.4.0   | setSyncWord takes single int (0x1424), not two bytes |
| 2026-03-11 | 2.4.0   | Universality audit: updater calls setup.sh, run.sh auto-setup, startup checks robust |
| 2026-03-11 | 2.4.0   | Version bump: LoRa APRS, .venv workflow, updater pip install |
| 2026-03-11 | 2.3.0   | Updater: pip install after git pull, setup.sh, run.sh         |
| 2026-03-11 | 2.3.0   | LoRa APRS 433 MHz support, balloon tracker cycles APRS+UKHAS |
| 2026-03-11 | 2.3.0   | README: venv setup, LoRa docs, updated requirements        |
| 2026-03-11 | 2.3.0   | LoRa sniffer, scanner, balloon tracker in Add-ons          |
| 2026-03-11 | 2.3.0   | AIO: bypass aiov2_ctl, direct GPIO via pinctrl (fix crash) |
| 2026-03-11 | 2.3.0   | Firmware version in sidebar + saved file fallback          |
| 2026-03-11 | 2.3.0   | Firmware version check at startup + update notification    |
| 2026-03-11 | FW 1.5.5| Merged upstream: OLED, WiGLE, ext GPS, attack viz, pwd dedup |
| 2026-03-10 | 2.3.0   | HC22000 auto-generation from HCCAPX                       |
| 2026-03-10 | 2.3.0   | Auto-update check on startup (GitHub)                     |
| 2026-03-10 | 2.3.0   | GitLab migration (primary), GitHub mirror                 |
| 2026-03-10 | 2.3.0   | Loot dashboard legend in README                           |
| 2026-03-09 | 2.2.0   | Portal file picker, sample portal embedded in code        |
| 2026-03-09 | 2.2.0   | GPS privacy mode, startup checks dialog                   |
