# JanOS-app

A Python TUI for controlling and interacting with **[JanOS](https://github.com/C5Lab/projectZero)** on ESP32-C5 devices.

## TUI Mode

Full-screen terminal interface with tabbed navigation, real-time data, and keyboard-driven controls. Built with [urwid](https://urwid.org/) for maximum terminal compatibility (SSH, serial consoles, ClockworkPi).

### Screenshots

**Home** — sidebar with GPS status, loot counters, network breakdown:

![Home idle with GPS](screenshots/home_idle_gps.png)

**Scan** — network discovery with RSSI color coding and Private Mode:

![Scan with Private Mode](screenshots/scan_private_mode.png)

**Sniffer** — live packet capture with AP/client tree and Private Mode:

![Sniffer with Private Mode](screenshots/sniffer_private_mode.png)

**Handshake Serial PCAP** — D-UCB sniffer with targeted deauth, PCAP streamed via serial:

![Handshake Serial Running](screenshots/handshake_serial_running.png)

**Custom Captive Portal** — file picker for loading custom HTML portal pages:

![Portal File Picker](screenshots/portal_file_picker.png)

### Install & Run
```bash
git clone https://github.com/LOCOSP/JanOS-app/
cd JanOS-app
./setup.sh                          # create .venv + install deps
./run.sh /dev/ttyUSB0               # run JanOS
```

**Manual setup** (if you prefer):
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m janos /dev/ttyUSB0
```

> **Note:** JanOS runs from a project virtual environment (`.venv/`). The `setup.sh` script creates it, installs all dependencies, and applies platform-specific fixes (e.g. Pi 5 GPIO shim). The auto-updater runs `setup.sh` after each `git pull` to keep everything in sync. `run.sh` will auto-run `setup.sh` on first launch if `.venv/` doesn't exist yet.

> **Raspberry Pi 5 / CM5:** The LoRa library (`LoRaRF`) requires GPIO access. On Pi 5, install the system shim first: `sudo apt install python3-rpi-lgpio python3-lgpio`. The `setup.sh` script detects Pi 5 and links the system packages into the venv automatically.

### ⚠️ Required Firmware

JanOS-app requires a compatible firmware on the ESP32-C5. The app communicates with the board over USB serial and needs features not available in the upstream projectZero firmware.

**Firmware releases:** [LOCOSP/projectZero](https://github.com/LOCOSP/projectZero/releases)

**Download the firmware binary:**
1. Go to the [latest release](https://github.com/LOCOSP/projectZero/releases/latest)
2. Download **`esp32c5-firmware.zip`** (~4 MB) — contains `bootloader.bin`, `projectZero.bin`, `partition-table.bin`, `oui_wifi.bin`, and `flash_board.py`

**Flash the ESP32-C5:**
```bash
pip install --upgrade esptool pyserial
python flash_board.py --port /dev/ttyUSB0          # Linux
python flash_board.py --port COM10                 # Windows
python flash_board.py --port /dev/ttyUSB0 --erase  # full erase before flash
```

> **Note:** The upstream [C5Lab/projectZero](https://github.com/C5Lab/projectZero) releases and web flasher at [c5lab.github.io/projectZero](https://c5lab.github.io/projectZero/) provide the mainline firmware which does **not** include handshake serial capture, custom portal upload (`set_html`), or other features required by this app. Always use the firmware from [LOCOSP/projectZero releases](https://github.com/LOCOSP/projectZero/releases).

### Keyboard Controls
| Key | Action |
|-----|--------|
| `1-4` | Switch tabs (Scan, Sniffer, Attacks, Add-ons) |
| `Tab` / `Shift+Tab` | Cycle tabs forward / backward |
| `Left` / `Right` | Switch tabs (D-pad navigation) |
| `Up` / `Down` | Navigate lists and tables |
| `s` | Start scan / sniffer / setup wizard / stop LoRa (context-dependent) |
| `Space` / `Enter` | Select / toggle item in tables (auto-sends to ESP32) |
| `r` | Fetch sniffer AP results |
| `p` | Fetch probe requests |
| `l` | Switch to live sniffer view |
| `x` | Clear results / clear log (context-dependent) |
| `d` | Show captured data (Portal / Evil Twin) |
| `6` | LoRa Sniffer — start/stop (Add-ons tab, LORA ON) |
| `7` | LoRa Scanner — start/stop (Add-ons tab, LORA ON) |
| `8` | Balloon Tracker — start/stop (Add-ons tab, LORA ON) |
| `Shift+M` | Toggle Mobile Mode (hide sidebar for small screens) |
| `Shift+P` | Toggle Private Mode (mask SSIDs, MACs, IPs, passwords) |
| `9` | Stop all running operations |
| `q` | Quit (confirmation prompt, sends stop to ESP32) |

### Features
- **Sidebar panel** -- left-side panel with JanOS ASCII logo, version, device status, runtime, loot counters (PCAP, HCCAPX, 22K, PWD, ET), network breakdown by band (2.4/5GHz) and auth type (WPA2/WPA3/Open)
- **Header bar** -- system stats: CPU temperature, RAM usage, load average
- **Mobile Mode** -- press `Shift+M` to hide the sidebar and go full-width for small screens (SSH from phone, narrow terminals)
- **Scan** -- scan networks, browse results with RSSI colors, select targets via keyboard
- **Sniffer** -- live packet counter, AP/client results, probe requests
- **Attacks** -- deauth, blackout, WPA3 SAE overflow, handshake capture, captive portal, evil twin — all in one tab with sub-screen navigation
- **Handshake Serial PCAP** -- capture WPA handshakes without SD card, PCAP/HCCAPX streamed as base64 via serial and auto-saved to loot
- **Handshake auto-rescan** -- when no network is selected, periodically rescans (45s cycle) so ESP32 discovers fresh networks as you move
- **Custom Captive Portals** -- load custom HTML portal pages from local `portals/` folder and send to ESP32 via chunked base64 serial transfer (see below)
- **Crash detection** -- automatic firmware crash alert overlay with state reset, dismissable with any key
- **Serial event loop** -- no background threads, uses urwid `watch_file()` for non-blocking serial I/O
- **Loot system** -- all captured data auto-saved to disk (see below)
- **Private Mode** -- press `Shift+P` to mask SSIDs, MACs, IPs, and passwords on screen (for recording/streaming). Loot files are NOT affected
- **Add-ons tab** -- extensible tools tab with **Flash ESP32-C5 Firmware**, **AIO v2 interface control** (toggle GPS/LORA/SDR/USB), and **LoRa tools** (sniffer, scanner, balloon tracker)
- **AIO v2 sidebar** -- live status of HackerGadgets AIO v2 GPIO interfaces (GPS, LORA, SDR, USB) displayed below loot section, auto-refreshed every 10s
- **LoRa SX1262** -- direct SPI communication with SX1262 radio on AIO v2 board for packet sniffing, frequency scanning, and balloon tracking (see below)

### Loot System

Every session automatically saves captured data to `loot/<timestamp>/`:

```
loot/
  2025-03-04_15-30-00/
    serial_full.log           # every ESP32 serial line (timestamped)
    scan_results.csv          # networks found during scan
    sniffer_aps.csv           # access points from sniffer
    sniffer_probes.csv        # captured probe requests
    handshakes/               # .pcap, .hccapx, and .22000 from serial capture
      HomeWifi_aabbccddeeff_153042.pcap
      HomeWifi_aabbccddeeff_153042.hccapx
      HomeWifi_aabbccddeeff_153042.22000
    portal_passwords.log      # portal form submissions (passwords, emails)
    evil_twin_capture.log     # evil twin captured data
    attacks.log               # attack start/stop events
    session_info.txt          # session summary (written on exit)
```

**What is saved automatically:**
- **Full serial log** -- every line from ESP32 with timestamp, always
- **Scan results** -- CSV with SSID, BSSID, channel, auth, RSSI, band, vendor
- **Sniffer data** -- APs (with client MACs) and probe requests as CSV
- **Handshakes** -- binary .pcap and .hccapx files decoded from base64 serial stream (hashcat-ready)
- **HC22000 hashes** -- `.22000` files auto-generated from complete handshakes (hashcat -m 22000), with GPS coordinates if available. Incomplete captures are skipped
- **Portal passwords** -- form submissions, usernames, emails
- **Evil Twin captures** -- passwords, handshakes
- **Attack events** -- start/stop with target info

The loot path is displayed in the footer status bar. Each app launch creates a new session directory.

### Loot Dashboard

The sidebar shows two loot lines:

```
Loot: PCAP:2 │ HCCAPX:2 │ 22K:2 │ PWD:1       ← current session
All:  S:103 │ PCAP:336 │ HCCAPX:10 │ 22K:8 │ PWD:2  ← all-time totals
```

| Abbrev | Meaning |
|--------|---------|
| **S** | Total sessions with at least one capture |
| **PCAP** | Raw packet captures (`.pcap` files from handshake capture) |
| **HCCAPX** | Hashcat-ready handshake files (`.hccapx`) |
| **22K** | Hashcat hc22000 hash files (`.22000`, only from complete handshakes) |
| **PWD** | Passwords collected via captive portal submissions |
| **ET** | Evil Twin credential captures |

All-time totals are persisted in `loot/loot_db.json` and updated automatically after every capture. The database is rebuilt from existing session directories on first run.

### Custom Captive Portals

You can create your own captive portal HTML pages and deploy them to the ESP32 without reflashing firmware.

**How it works:**
1. Place `.html` files in the `portals/` directory (next to `janos/`)
2. In the Portal tab, press `s` to start the setup wizard
3. Enter SSID name for the fake access point
4. Choose `n` (No) when asked about built-in portal
5. Select your custom HTML from the file picker
6. The HTML is base64-encoded and sent to ESP32 via serial (`set_html` protocol)
7. Confirm to start — the portal serves your custom page

**Creating portal pages:**
- Must be a single self-contained HTML file (inline CSS/JS, no external resources)
- Form must POST to `/login` with a `password` field for credential capture
- Embedded images should use base64 data URIs
- Maximum size: ~768 KB (1 MB base64 buffer on ESP32 PSRAM)
- A sample `Custom-portal.html` is included in `portals/`

**Example form structure:**
```html
<form method="POST" action="/login">
  <input type="email" name="email" placeholder="Email">
  <input type="password" name="password" placeholder="Password">
  <button type="submit">Connect</button>
</form>
```

**Firmware requirement:** Requires JanOS firmware with `set_html` chunked protocol support — see [LOCOSP/projectZero releases](https://github.com/LOCOSP/projectZero/releases).

### Add-ons: Flash Firmware

The **Add-ons** tab (key `4`) provides a built-in firmware flasher for the ESP32-C5.

**How it works:**
1. Switch to the Add-ons tab (`4`)
2. Press `1` to start Flash Firmware
3. Confirm the dialog — JanOS closes the serial port, downloads the latest firmware release from GitHub, and flashes it via `esptool`
4. Live progress is shown in the log (download %, esptool output, flash status)
5. After flashing, esptool auto-resets the ESP32 via RTS/DTR and JanOS reconnects serial

**Requirements:** `esptool` must be installed (`pip install esptool`). The ESP32-C5 must be connected via a USB-UART bridge (e.g., CP2102N) that supports RTS/DTR auto-reset — no BOOT button or replug needed.

### Add-ons: AIO v2 Control

The **Add-ons** tab integrates with **[HackerGadgets AIO v2](https://github.com/hackergadgets/aiov2_ctl)** — a GPIO expansion module for ClockworkPi uConsole with 4 switchable interfaces: GPS (GPIO27), LORA (GPIO16), SDR (GPIO7), USB (GPIO23).

**Sidebar status** (below Loot section):
```
AIO  GPS:ON │ LORA:OFF │ SDR:OFF │ USB:OFF
```

**Toggle interfaces** from the Add-ons tab:
1. Switch to Add-ons (`4`)
2. Press `2`-`5` to toggle GPS / LORA / SDR / USB on or off
3. Sidebar updates immediately, status auto-refreshes every 10 seconds

**Install `aiov2_ctl`** — if not already installed, Add-ons shows `[2] Install AIO v2 Control` which installs directly from GitHub with live progress log.

**Startup check** reports AIO v2 availability: `[OK] AIO v2 (pinctrl)` or `[--] AIO v2 not available`.

### Add-ons: LoRa SX1262

The **Add-ons** tab provides LoRa radio tools when the **LORA** GPIO interface is enabled (`[3] LORA [ON]`). Uses direct SPI communication with the SX1262 chip on the AIO v2 board via the `LoRaRF` library.

**Available tools** (keys `6`-`8`, visible only when LORA is ON):

| Key | Tool | Description |
|-----|------|-------------|
| `6` | **LoRa Sniffer** | Listen on a single frequency (default 868.1 MHz SF7 BW125k). Shows raw packets with hex + ASCII, RSSI, SNR |
| `7` | **LoRa Scanner** | Cycle through all EU868 (8 freqs) + APRS 433 (3 freqs) frequencies × 6 spreading factors. Detects any active LoRa transmissions |
| `8` | **Balloon Tracker** | Cycle LoRa APRS (433.775 SF12, 434.855 SF9) and UKHAS (868.1 SF8) profiles. Auto-parses APRS position/altitude and UKHAS CSV payloads |

**Balloon Tracker** supports two payload formats:
- **LoRa APRS** — `CALL>DEST:=DDMM.MMN/DDDMM.MMEO .../A=AAAAAA` (position in degrees+minutes, altitude in feet). Ref: [SQ2CPA/LoRa_APRS_Balloon](https://github.com/SQ2CPA/LoRa_APRS_Balloon)
- **UKHAS CSV** — `$$CALL,ID,TIME,LAT,LON,ALT,...` (comma-separated with optional `$$` prefix)

**Controls**: press the same key again or `s` to stop a running LoRa operation. Toggling LORA OFF auto-stops any running LoRa tool.

**Hardware**: SX1262 on `/dev/spidev1.0` (SPI bus 1, CS 0). IRQ=GPIO26, Busy=GPIO24, Reset=GPIO25. User must be in `spi` group.

### Flags
```
./run.sh /dev/ttyUSB0 --debug    # Log to /tmp/janos.log
./run.sh /dev/ttyUSB0 --legacy   # Fall back to old CLI
```

### Requirements
- Python 3.10+
- `urwid >= 2.1.0` — TUI framework
- `pyserial >= 3.5` — ESP32 serial communication
- `LoRaRF >= 1.4.0` — SX1262 SPI radio control (optional, for LoRa features)
- `esptool >= 4.0` — ESP32 firmware flashing (optional, for Add-ons flash)
- Works on serial terminals, SSH, and ClockworkPi uConsole

## Legacy CLI Mode

The original `JanOS_app.py` is still available as a standalone script:

```bash
chmod +x JanOS_app.py
python3 JanOS_app.py /dev/ttyUSB0
```

## Desktop Shortcut (Fullscreen Launcher)

You can set up a desktop shortcut on ClockworkPi uConsole that launches JanOS in fullscreen (no window decorations):

**1. Create the launch script** (`janos-launch.sh` is included in the repo):
```bash
#!/bin/bash
cd "$(dirname "$0")"
exec lxterminal --title=JanOS --no-remote -e bash -c '.venv/bin/python3 -m janos /dev/ttyUSB0; read -p "Press Enter..."'
```
```bash
chmod +x janos-launch.sh
```

**2. Create a `.desktop` file** on the desktop (adjust paths to your install location):
```bash
JANOS_DIR="$(pwd)"  # run from the JanOS-app directory
cat > ~/Desktop/JanOS.desktop << EOF
[Desktop Entry]
Name=JanOS
Comment=WiFi Audit Tool for ESP32
Exec=$JANOS_DIR/janos-launch.sh
Icon=$JANOS_DIR/assets/janos-icon.svg
Terminal=false
Type=Application
Categories=Utility;Security;
StartupNotify=true
EOF
chmod +x ~/Desktop/JanOS.desktop
```

**3. Auto-fullscreen via labwc window rule** (Raspberry Pi OS Bookworm with Wayland):

Add to `~/.config/labwc/rc.xml` before `</openbox_config>`:
```xml
<windowRules>
  <windowRule title="JanOS">
    <action name="ToggleFullscreen"/>
  </windowRule>
</windowRules>
```
Then reload: `kill -SIGHUP $(pidof labwc)`

**4. Suppress "Execute File?" dialog** (optional):

Create `~/.config/libfm/libfm.conf`:
```ini
[config]
quick_exec=1
```
Then restart PCManFM: `killall pcmanfm` (it auto-respawns).

## Hardware

Designed for **ClockworkPi uConsole** with ESP32-C5-WROOM-1 connected via USB serial. The D-pad and keyboard map directly to TUI navigation.
