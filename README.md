# JanOS-app
![IMG_7409](https://github.com/user-attachments/assets/6d367f35-297a-44ec-8572-a710b3c725ee)

A Python TUI for controlling and interacting with **[JanOS](https://github.com/C5Lab/projectZero)** on ESP32-C5 devices.

## TUI Mode (new)

Full-screen terminal interface with tabbed navigation, real-time data, and keyboard-driven controls.

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
| `Tab` / `Shift+Tab` | Cycle tabs |
| `s` | Start scan / sniffer / setup wizard (context-dependent) |
| `Space` / `Enter` | Select / toggle item in tables |
| `a` | Apply network selection (Scan tab) |
| `r` | Fetch sniffer AP results |
| `p` | Fetch probe requests |
| `9` | Stop all attacks |
| `q` | Quit |

### Features
- **Scan** — scan networks, browse results with RSSI colors, select targets
- **Sniffer** — live packet counter, AP/client results, probe requests
- **Attacks** — deauth, blackout, WPA3 SAE overflow, handshake capture (with confirmation dialogs)
- **Portal** — captive portal setup wizard (SSID, HTML file pick from SD card), live monitoring
- **Evil Twin** — target network selection, HTML pick, live monitoring with captured data
- **Crash detection** — automatic firmware crash alert overlay, state reset
- **Serial event loop** — no background threads, uses urwid `watch_file()` for non-blocking serial I/O

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

## Status

Work in progress — more features and supported boards will be added over time.

### JanOS_dev status
`JanOS_dev_0.0.1.py` is under active development and currently unstable.
