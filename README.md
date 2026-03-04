# JanOS-app
![IMG_7409](https://github.com/user-attachments/assets/6d367f35-297a-44ec-8572-a710b3c725ee)

A Python TUI for controlling and interacting with **[JanOS](https://github.com/C5Lab/projectZero)** on ESP32-C5 devices.

## TUI Mode

Full-screen terminal interface with tabbed navigation, real-time data, and keyboard-driven controls. Built with [urwid](https://urwid.org/) for maximum terminal compatibility (SSH, serial consoles, ClockworkPi).

### Screenshots

**Scan tab** — network discovery with RSSI color coding and Private Mode:

![Scan with Private Mode](screenshots/scan_private_mode.png)

**Handshake Serial Capture** — confirmation dialog before starting SD-less capture:

![Handshake Confirm](screenshots/handshake_confirm.png)

**Handshake Serial PCAP** — live D-UCB sniffer with targeted deauth, PCAP streamed via serial:

![Handshake Serial Running](screenshots/handshake_serial_running.png)

### Install & Run
```bash
git clone https://github.com/LOCOSP/JanOS-app/
cd JanOS-app
pip install -r requirements.txt
python3 -m janos /dev/ttyUSB0
```

### Keyboard Controls
| Key | Action |
|-----|--------|
| `1-5` | Switch tabs (Scan, Sniffer, Attacks, Portal, Evil Twin) |
| `Tab` / `Shift+Tab` | Cycle tabs forward / backward |
| `Left` / `Right` | Switch tabs (D-pad navigation) |
| `Up` / `Down` | Navigate lists and tables |
| `s` | Start scan / sniffer / setup wizard (context-dependent) |
| `Space` / `Enter` | Select / toggle item in tables (auto-sends to ESP32) |
| `r` | Fetch sniffer AP results |
| `p` | Fetch probe requests |
| `l` | Switch to live sniffer view |
| `x` | Clear results / clear log (context-dependent) |
| `d` | Show captured data (Portal / Evil Twin) |
| `P` | Toggle Private Mode (mask SSIDs, MACs, IPs, passwords) |
| `9` | Stop all running operations |
| `q` | Quit (sends stop to ESP32) |

### Features
- **Scan** -- scan networks, browse results with RSSI colors, select targets via keyboard
- **Sniffer** -- live packet counter, AP/client results, probe requests
- **Attacks** -- deauth, blackout, WPA3 SAE overflow, handshake capture with live ESP32 output log
- **Handshake Serial PCAP** -- capture WPA handshakes without SD card, PCAP/HCCAPX streamed as base64 via serial and auto-saved to loot
- **Portal** -- captive portal setup wizard (SSID, HTML file pick from SD card), live monitoring
- **Evil Twin** -- target network selection, HTML pick, live monitoring with captured data
- **Crash detection** -- automatic firmware crash alert overlay with state reset
- **Serial event loop** -- no background threads, uses urwid `watch_file()` for non-blocking serial I/O
- **Loot system** -- all captured data auto-saved to disk (see below)
- **Private Mode** -- press `P` to mask SSIDs, MACs, IPs, and passwords on screen (for recording/streaming). Loot files are NOT affected

### Loot System

Every session automatically saves captured data to `loot/<timestamp>/`:

```
loot/
  2025-03-04_15-30-00/
    serial_full.log           # every ESP32 serial line (timestamped)
    scan_results.csv          # networks found during scan
    sniffer_aps.csv           # access points from sniffer
    sniffer_probes.csv        # captured probe requests
    handshakes/               # .pcap and .hccapx from serial capture
      HomeWifi_aabbccddeeff_153042.pcap
      HomeWifi_aabbccddeeff_153042.hccapx
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
- **Portal passwords** -- form submissions, usernames, emails
- **Evil Twin captures** -- passwords, handshakes
- **Attack events** -- start/stop with target info

The loot path is displayed in the footer status bar. Each app launch creates a new session directory.

### Flags
```
python3 -m janos /dev/ttyUSB0 --debug    # Log to /tmp/janos.log
python3 -m janos /dev/ttyUSB0 --legacy   # Fall back to old CLI
```

### Requirements
- Python 3.10+
- `urwid >= 2.1.0`
- `pyserial >= 3.5`
- Works on serial terminals, SSH, and ClockworkPi uConsole

## Legacy CLI Mode

The original `JanOS_app.py` is still available as a standalone script:

```bash
chmod +x JanOS_app.py
python3 JanOS_app.py /dev/ttyUSB0
```

## Hardware

Designed for **ClockworkPi uConsole** with ESP32-C5-WROOM-1 connected via USB serial. The D-pad and keyboard map directly to TUI navigation.
