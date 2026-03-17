"""Attacks screen — WiFi & Bluetooth attacks with confirmation + live log."""

import re
import threading
import time
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...privacy import mask_line, mask_mac
from ...config import (
    CMD_START_DEAUTH,
    CMD_START_BLACKOUT,
    CMD_SAE_OVERFLOW,
    CMD_START_HANDSHAKE,
    CMD_START_HANDSHAKE_SERIAL,
    CMD_SCAN_BT,
    CMD_SCAN_AIRTAG,
    CMD_STOP,
    HS_RESCAN_INTERVAL,
)
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.text_input_dialog import TextInputDialog
from ..widgets.log_viewer import LogViewer


WIFI_ATTACKS = [
    ("1", "Deauth Attack",           CMD_START_DEAUTH,           "attack_running"),
    ("2", "Blackout Attack",         CMD_START_BLACKOUT,          "blackout_running"),
    ("3", "WPA3 SAE Overflow",       CMD_SAE_OVERFLOW,            "sae_overflow_running"),
    ("4", "Handshake Capture",       CMD_START_HANDSHAKE,         "handshake_running"),
    ("5", "Handshake No SD Card",    CMD_START_HANDSHAKE_SERIAL,  "handshake_running"),
    ("6", "Captive Portal",          None,                        "portal_running"),
    ("7", "Evil Twin",               None,                        "evil_twin_running"),
]

BT_ATTACKS = [
    ("b", "BLE Scan (10s)",          CMD_SCAN_BT,                 "bt_scan_running"),
    ("t", "BLE Tracker",             None,                        "bt_tracking_running"),
    ("a", "AirTag Scanner",          CMD_SCAN_AIRTAG,             "bt_airtag_running"),
]

ADVANCED_ATTACKS = [
    ("d", "Dragon Drain (WPA3 DoS)", None,                        "dragon_drain_running"),
    ("m", "MITM (ARP Spoofing)",     None,                        "mitm_running"),
    ("k", "BlueDucky (BT HID)",      None,                        "bt_ducky_running"),
    ("j", "RACE (Headphone Jack)",   None,                        "race_running"),
]

# Combined for flag iteration
ALL_ATTACKS = WIFI_ATTACKS + BT_ATTACKS + ADVANCED_ATTACKS


class AttackItem(urwid.WidgetWrap):
    """Single attack list item (non-selectable — use number keys to pick)."""

    def __init__(self, key: str, label: str, active: bool) -> None:
        if active:
            text = urwid.Text(("attack_active", f"  [{key}] {label}  [RUNNING]"))
        else:
            text = urwid.Text(("default", f"  [{key}] {label}"))
        super().__init__(text)


class AttacksScreen(urwid.WidgetWrap):
    """Attack list — number keys start attacks (with confirm), 9 stops all.
    Includes a live serial log showing ESP32 feedback during attacks."""

    def __init__(self, state: AppState, serial: SerialManager, app,
                 loot: LootManager | None = None,
                 portal=None, evil_twin=None,
                 dragon_drain=None, mitm=None, bt_ducky=None,
                 race_attack=None) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot
        self._portal = portal
        self._evil_twin = evil_twin
        self._dragon_drain = dragon_drain
        self._mitm = mitm
        self._bt_ducky = bt_ducky
        self._race_attack = race_attack
        self._sub_screen = None  # active sub-screen or None

        # Handshake auto-rescan state (cycle when no network selected)
        self._hs_cmd_running: str = ""      # which HS command is active
        self._hs_cycle_time: float = 0.0    # when current cycle started
        self._hs_restarting: bool = False    # waiting for restart delay
        self._hs_restart_at: float = 0.0    # when to send start again

        # BT device parser regex: "  1. AA:BB:CC:DD:EE:FF  RSSI: -42 dBm  Name: Foo"
        self._bt_device_re = re.compile(
            r'^\s*\d+\.\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)\s*dBm'
            r'(?:\s+Name:\s*(.+?))?(\s*\[AirTag\]|\s*\[SmartTag\])?\s*$'
        )

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [1-7]WiFi  [b/t/a]BT  [d/m/k/j]Adv  [9]Stop  [x]Clear"))
        self._last_flags = ""  # track state changes to avoid needless rebuilds

        log_label = urwid.AttrMap(
            urwid.Text(("dim", "  ── ESP32 Output ──")), "default"
        )

        self._menu_view = urwid.Pile([
            ("fixed", 16, self._listbox),
            ("pack", log_label),
            self._log,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        self._body = urwid.WidgetPlaceholder(self._menu_view)
        super().__init__(self._body)
        self._rebuild()

    def refresh(self) -> None:
        # If sub-screen is active, delegate refresh
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "refresh"):
                self._sub_screen.refresh()
            return

        # Handshake auto-rescan check (runs every second)
        self._check_hs_rescan()

        # Only rebuild when attack flags actually change
        flags = self._get_flags_key()
        if flags != self._last_flags:
            self._last_flags = flags
            self._rebuild()

        # Update status bar
        self._update_status()

    def _get_flags_key(self) -> str:
        return ",".join(
            str(getattr(self.state, flag, False)) for _, _, _, flag in ALL_ATTACKS
        )

    def _rebuild(self) -> None:
        self._walker.clear()
        # WiFi section
        self._walker.append(urwid.Text(("dim", "  ── WiFi ──")))
        for key, label, cmd, flag in WIFI_ATTACKS:
            active = getattr(self.state, flag, False)
            self._walker.append(AttackItem(key, label, active))
        # Bluetooth section
        self._walker.append(urwid.Text(("dim", "  ── Bluetooth ──")))
        for key, label, cmd, flag in BT_ATTACKS:
            active = getattr(self.state, flag, False)
            self._walker.append(AttackItem(key, label, active))
        # Advanced section (Python-native, no ESP32)
        self._walker.append(urwid.Text(("dim", "  ── Advanced ──")))
        for key, label, cmd, flag in ADVANCED_ATTACKS:
            active = getattr(self.state, flag, False)
            self._walker.append(AttackItem(key, label, active))

    def _update_status(self) -> None:
        sel = self.state.selected_networks
        # Deduplicate running labels
        seen = set()
        running = []
        for _, label, _, flag in ALL_ATTACKS:
            if getattr(self.state, flag, False) and flag not in seen:
                seen.add(flag)
                running.append(label)

        if self.state.bt_tracking_running and self.state.bt_tracking_mac:
            running_str = ", ".join(running)
            self._status.set_text(
                ("attack_active", f"  ACTIVE: {running_str} | [9]Stop all  [x]Clear")
            )
        elif self.state.bt_airtag_running:
            at = self.state.bt_airtags
            st = self.state.bt_smarttags
            self._status.set_text(
                ("attack_active", f"  AirTags:{at} │ SmartTags:{st} | [9]Stop  [x]Clear")
            )
        elif running:
            run_str = ", ".join(running)
            self._status.set_text(
                ("attack_active", f"  ACTIVE: {run_str} | [9]Stop all  [x]Clear")
            )
        elif sel:
            self._status.set_text(("dim", f"  Target: {sel} | [1-7]WiFi  [b/t/a]BT  [d/m/k/j]Adv  [9]Stop  [x]Clear"))
        else:
            self._status.set_text(("dim", f"  [1-7]WiFi  [b/t/a]BT  [d/m/k/j]Adv  [9]Stop  [x]Clear"))

    def handle_serial_line(self, line: str) -> None:
        """Route serial data to appropriate handler."""
        # If a sub-screen is currently displayed, forward everything to it
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "handle_serial_line"):
                self._sub_screen.handle_serial_line(line)
            return

        # Forward to portal if running (even when viewing attack menu)
        if self.state.portal_running and self._portal:
            self._portal.handle_serial_line(line)
            return

        # Forward to evil twin if running (even when viewing attack menu)
        if self.state.evil_twin_running and self._evil_twin:
            self._evil_twin.handle_serial_line(line)
            return

        # BT serial handling
        if self._handle_bt_serial(line):
            return

        # Original: show in log for basic attacks
        if not self.state.any_attack_running():
            return
        # Color-code output (use raw line for detection, masked for display)
        line_lower = line.lower()
        if "error" in line_lower or "fail" in line_lower:
            attr = "error"
        elif "deauth" in line_lower or "handshake" in line_lower or "capture" in line_lower:
            attr = "attack_active"
        elif "sent" in line_lower or "ok" in line_lower or "success" in line_lower:
            attr = "success"
        else:
            attr = "dim"
        self._log.append(mask_line(line.strip()), attr)

    # ------------------------------------------------------------------
    # Bluetooth serial parsing
    # ------------------------------------------------------------------

    def _handle_bt_serial(self, line: str) -> bool:
        """Parse BT serial output. Returns True if line was consumed."""
        stripped = line.strip()

        # BLE Scan (one-time 10s)
        if self.state.bt_scan_running:
            if "BLE scan starting" in stripped:
                self._log.append(">>> BLE scan started (10s)...", "success")
                return True
            if "=== BLE Scan Results ===" in stripped:
                self._log.append("── BLE Scan Results ──", "attack_active")
                return True
            if stripped.startswith("Found ") and "devices:" in stripped:
                self._log.append(f"  {mask_line(stripped)}", "dim")
                return True
            m = self._bt_device_re.match(stripped)
            if m:
                mac, rssi_s, name, tag = m.groups()
                rssi = int(rssi_s)
                name = (name or "").strip()
                is_airtag = tag and "AirTag" in tag
                is_smarttag = tag and "SmartTag" in tag
                # Color by type
                if is_airtag or is_smarttag:
                    attr = "warning"
                elif rssi > -50:
                    attr = "success"
                elif rssi > -70:
                    attr = "default"
                else:
                    attr = "dim"
                display = f"  {mask_mac(mac)}  RSSI:{rssi}dBm"
                if name:
                    display += f"  {name}"
                if is_airtag:
                    display += "  [AirTag]"
                elif is_smarttag:
                    display += "  [SmartTag]"
                self._log.append(display, attr)
                self.state.bt_devices += 1
                if self._loot:
                    self._loot.save_bt_device(mac, rssi, name, bool(is_airtag), bool(is_smarttag))
                return True
            if stripped.startswith("Summary:"):
                self._log.append(f"  {mask_line(stripped)}", "attack_active")
                self.state.bt_scan_running = False
                self._last_flags = ""
                return True
            # Pass through other BLE scan lines
            if self.state.bt_scan_running:
                self._log.append(f"  {mask_line(stripped)}", "dim")
                return True

        # BLE Tracker (continuous RSSI)
        if self.state.bt_tracking_running:
            mac = self.state.bt_tracking_mac
            if "Tracking" in stripped and mac.upper() in stripped.upper():
                self._log.append(f">>> Tracking {mask_mac(mac)}...", "success")
                return True
            if "not found" in stripped:
                self._log.append(f"  {mask_mac(mac)}  not found", "error")
                return True
            if "RSSI:" in stripped:
                try:
                    rssi = int(re.search(r'RSSI:\s*(-?\d+)', stripped).group(1))
                    name_m = re.search(r'Name:\s*(.+)', stripped)
                    name = name_m.group(1).strip() if name_m else ""
                    if rssi > -50:
                        attr = "success"
                    elif rssi > -70:
                        attr = "default"
                    else:
                        attr = "warning"
                    display = f"  {mask_mac(mac)}  RSSI:{rssi}dBm"
                    if name:
                        display += f"  {name}"
                    self._log.append(display, attr)
                except (AttributeError, ValueError):
                    self._log.append(f"  {mask_line(stripped)}", "dim")
                return True
            if "stopped" in stripped.lower():
                self.state.bt_tracking_running = False
                self._last_flags = ""
                return True

        # AirTag Scanner (continuous count)
        if self.state.bt_airtag_running:
            if "AirTag scanner starting" in stripped:
                self._log.append(">>> AirTag scanner started...", "success")
                return True
            if "Output format:" in stripped or "Use 'stop'" in stripped:
                return True  # skip info lines
            # Parse "X,Y" count format
            count_m = re.match(r'^(\d+),(\d+)$', stripped)
            if count_m:
                at = int(count_m.group(1))
                st = int(count_m.group(2))
                self.state.bt_airtags = at
                self.state.bt_smarttags = st
                attr = "warning" if (at > 0 or st > 0) else "dim"
                self._log.append(f"  AirTags:{at} │ SmartTags:{st}", attr)
                if self._loot and (at > 0 or st > 0):
                    self._loot.save_bt_airtag_event(at, st)
                return True
            if "stopped" in stripped.lower():
                self.state.bt_airtag_running = False
                self._last_flags = ""
                return True

        return False

    # ------------------------------------------------------------------
    # Sub-screen management (Portal / Evil Twin)
    # ------------------------------------------------------------------

    def _enter_sub_screen(self, screen) -> None:
        """Switch body to show a sub-screen."""
        self._sub_screen = screen
        self._body.original_widget = screen

    def _exit_sub_screen(self) -> None:
        """Return to the attacks menu."""
        self._sub_screen = None
        self._body.original_widget = self._menu_view
        self._last_flags = ""  # force menu rebuild

    # ------------------------------------------------------------------
    # Handshake auto-rescan (cycle when no networks selected)
    # ------------------------------------------------------------------

    def _check_hs_rescan(self) -> None:
        """Non-blocking auto-rescan: stop → 1.5s delay → restart."""
        # Phase 2: delayed restart after stop
        if self._hs_restarting:
            if time.time() >= self._hs_restart_at:
                self._hs_restarting = False
                self.serial.read_available()  # drain stale buffer
                self.serial.send_command(self._hs_cmd_running)
                self._hs_cycle_time = time.time()
                self._log.append(
                    ">>> Handshake restarted (fresh network scan)", "attack_active"
                )
                if self._loot:
                    self._loot.log_attack_event("RESCAN: Handshake restarted")
            return  # don't check for new cycle while restarting

        # Phase 1: check if it's time to cycle
        if not (self.state.handshake_running and self._hs_cmd_running):
            return
        if self.state.selected_networks:
            return  # focused mode — user selected specific targets
        if self._hs_cycle_time <= 0:
            return
        if time.time() - self._hs_cycle_time < HS_RESCAN_INTERVAL:
            return

        # Trigger rescan: stop → wait → restart
        self.serial.send_command(CMD_STOP)
        self._hs_restarting = True
        self._hs_restart_at = time.time() + 1.5
        self._log.append(
            f">>> Auto-rescan ({HS_RESCAN_INTERVAL}s) — cycling handshake...", "dim"
        )

    def _reset_hs_rescan(self) -> None:
        """Clear auto-rescan state."""
        self._hs_cmd_running = ""
        self._hs_cycle_time = 0.0
        self._hs_restarting = False
        self._hs_restart_at = 0.0

    # ------------------------------------------------------------------
    # WiFi attack start
    # ------------------------------------------------------------------

    def _start_wifi_attack(self, idx: int) -> None:
        if idx >= len(WIFI_ATTACKS):
            return
        key, label, cmd, flag = WIFI_ATTACKS[idx]

        # Portal → sub-screen
        if cmd is None and flag == "portal_running" and self._portal:
            self._enter_sub_screen(self._portal)
            return

        # Evil Twin → sub-screen
        if cmd is None and flag == "evil_twin_running" and self._evil_twin:
            self._enter_sub_screen(self._evil_twin)
            return

        # ESP32 attacks require serial connection
        if not self.state.connected:
            self._app.wait_for_esp32(lambda: self._start_wifi_attack(idx))
            return

        # Handshake modes can work without selection (auto-rescan)
        is_handshake = cmd in (CMD_START_HANDSHAKE, CMD_START_HANDSHAKE_SERIAL)
        serial_mode = (cmd == CMD_START_HANDSHAKE_SERIAL)
        if not is_handshake and not self.state.selected_networks:
            self._status.set_text(("error", "  Select networks first (Scan tab)"))
            return

        if getattr(self.state, flag, False):
            self._status.set_text(("warning", f"  {label} already running"))
            return

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                # Ensure ESP32 is idle — stop + wait for cleanup (pcap dump ~1s)
                self.serial.send_command(CMD_STOP)
                self.state.stop_all()
                time.sleep(1.5)
                self.serial.read_available()  # drain stale data
                self.serial.send_command(cmd)
                setattr(self.state, flag, True)
                self._status.set_text(("attack_active", f"  {label} STARTED"))

                # Handshake auto-rescan setup
                if is_handshake:
                    self._hs_cmd_running = cmd
                    self._hs_cycle_time = time.time()
                    if self.state.selected_networks:
                        self._log.append(
                            f">>> {label} — focused on selected networks",
                            "attack_active",
                        )
                    else:
                        self._log.append(
                            f">>> {label} — auto-rescan every {HS_RESCAN_INTERVAL}s (no selection)",
                            "attack_active",
                        )
                elif serial_mode:
                    self._log.append(
                        ">>> Handshake Serial started — PCAP auto-saved to loot/handshakes/",
                        "attack_active",
                    )
                else:
                    self._log.append(
                        f">>> {label} started — waiting for ESP32 output...",
                        "attack_active",
                    )

                self._last_flags = ""  # force rebuild
                if self._loot:
                    targets = self.state.selected_networks or "all (auto-rescan)"
                    self._loot.log_attack_event(f"STARTED: {label} (targets: {targets})")
            else:
                self._status.set_text(("dim", f"  {label} cancelled"))

        # Build confirmation message
        if is_handshake and not self.state.selected_networks:
            confirm_msg = (
                f"Start {label}?\n"
                f"No networks selected — will auto-rescan\n"
                f"every {HS_RESCAN_INTERVAL}s for fresh targets."
            )
        elif serial_mode:
            confirm_msg = (
                f"Start {label}?\n"
                f"Will attack all visible networks.\n"
                f"PCAP saved to loot/handshakes/"
            )
        else:
            confirm_msg = f"Start {label}?"
        dialog = ConfirmDialog(confirm_msg, on_confirm)
        self._app.show_overlay(dialog, 55, 10 if is_handshake else 8)

    # ------------------------------------------------------------------
    # Bluetooth attack start
    # ------------------------------------------------------------------

    def _start_bt_scan(self) -> None:
        """Start one-time BLE scan (10s). No confirmation needed."""
        if not self.state.connected:
            self._app.wait_for_esp32(self._start_bt_scan)
            return
        if self.state.bt_scan_running:
            return
        # Stop any running operation first
        self.serial.send_command(CMD_STOP)
        self.state.stop_all()
        time.sleep(0.5)
        self.serial.read_available()
        self.serial.send_command(CMD_SCAN_BT)
        self.state.bt_scan_running = True
        self.state.bt_devices = 0
        self._last_flags = ""
        if self._loot:
            self._loot.log_attack_event("STARTED: BLE Scan")

    def _start_bt_tracker(self) -> None:
        """Show MAC input dialog, then start BLE tracking."""
        if not self.state.connected:
            self._app.wait_for_esp32(self._start_bt_tracker)
            return
        def on_input(mac: str) -> None:
            self._app.dismiss_overlay()
            mac = mac.strip().upper()
            if not re.match(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$', mac):
                self._status.set_text(("error", "  Invalid MAC format (XX:XX:XX:XX:XX:XX)"))
                return
            self.serial.send_command(CMD_STOP)
            self.state.stop_all()
            time.sleep(0.5)
            self.serial.read_available()
            self.serial.send_command(f"{CMD_SCAN_BT} {mac}")
            self.state.bt_tracking_running = True
            self.state.bt_tracking_mac = mac
            self._last_flags = ""
            self._log.append(f">>> BLE Tracker: {mask_mac(mac)}", "success")
            if self._loot:
                self._loot.log_attack_event(f"STARTED: BLE Tracker ({mac})")  # loot = full data

        def on_dialog(text) -> None:
            if text is None:
                self._app.dismiss_overlay()
                return
            on_input(text)

        dialog = TextInputDialog(
            "BLE MAC address (XX:XX:XX:XX:XX:XX):",
            on_dialog,
        )
        self._app.show_overlay(dialog, 50, 7)

    def _start_bt_airtag(self) -> None:
        """Start continuous AirTag/SmartTag scanner."""
        if not self.state.connected:
            self._app.wait_for_esp32(self._start_bt_airtag)
            return
        if self.state.bt_airtag_running:
            return

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self.serial.send_command(CMD_STOP)
                self.state.stop_all()
                time.sleep(0.5)
                self.serial.read_available()
                self.serial.send_command(CMD_SCAN_AIRTAG)
                self.state.bt_airtag_running = True
                self.state.bt_airtags = 0
                self.state.bt_smarttags = 0
                self._last_flags = ""
                if self._loot:
                    self._loot.log_attack_event("STARTED: AirTag Scanner")

        dialog = ConfirmDialog(
            "Start AirTag Scanner?\n"
            "Continuous BLE scan for Apple AirTags\n"
            "and Samsung SmartTags.",
            on_confirm
        )
        self._app.show_overlay(dialog, 50, 9)

    # ------------------------------------------------------------------
    # Stop all
    # ------------------------------------------------------------------

    def _stop_all(self) -> None:
        if self.serial:
            self.serial.send_command(CMD_STOP)
        # Stop Python-native attacks
        if self._dragon_drain and hasattr(self._dragon_drain, '_stop'):
            self._dragon_drain._stop()
        if self._mitm and hasattr(self._mitm, '_stop'):
            self._mitm._stop()
        if self._bt_ducky and hasattr(self._bt_ducky, '_stop'):
            self._bt_ducky._stop()
        if self._race_attack and hasattr(self._race_attack, '_stop'):
            self._race_attack._stop()
        self.state.stop_all()
        self._reset_hs_rescan()
        self._log.append(">>> All attacks STOPPED", "warning")
        self._status.set_text(("success", "  All attacks stopped"))
        self._last_flags = ""  # force rebuild
        if self._loot:
            self._loot.log_attack_event("STOPPED: All attacks")

    def keypress(self, size, key):
        # Sub-screen mode: forward keys, Esc returns to menu
        if self._sub_screen is not None:
            result = self._sub_screen.keypress(size, key)
            if result is None:
                return None  # sub-screen consumed it
            if key == "esc":
                self._exit_sub_screen()
                return None
            return result  # bubble up (e.g. "9" → global stop)

        # WiFi attacks (1-7)
        if key in ("1", "2", "3", "4", "5", "6", "7"):
            self._start_wifi_attack(int(key) - 1)
            return None

        # Bluetooth attacks
        if key == "b":
            self._start_bt_scan()
            return None
        if key == "t":
            self._start_bt_tracker()
            return None
        if key == "a":
            self._start_bt_airtag()
            return None

        # Advanced attacks (Python-native, no ESP32)
        if key == "d" and self._dragon_drain:
            self._enter_sub_screen(self._dragon_drain)
            return None
        if key == "m" and self._mitm:
            self._enter_sub_screen(self._mitm)
            return None
        if key == "k" and self._bt_ducky:
            self._enter_sub_screen(self._bt_ducky)
            return None
        if key == "j" and self._race_attack:
            self._enter_sub_screen(self._race_attack)
            return None

        if key == "9":
            self._stop_all()
            return None
        if key == "x":
            self._log.clear()
            return None
        return super().keypress(size, key)
