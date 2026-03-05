"""Attacks screen — start/stop attack types with confirmation + live log."""

import time
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...privacy import mask_line
from ...config import (
    CMD_START_DEAUTH,
    CMD_START_BLACKOUT,
    CMD_SAE_OVERFLOW,
    CMD_START_HANDSHAKE,
    CMD_START_HANDSHAKE_SERIAL,
    CMD_STOP,
)
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.log_viewer import LogViewer


ATTACKS = [
    ("1", "Deauth Attack",           CMD_START_DEAUTH,           "attack_running"),
    ("2", "Blackout Attack",         CMD_START_BLACKOUT,          "blackout_running"),
    ("3", "WPA3 SAE Overflow",       CMD_SAE_OVERFLOW,            "sae_overflow_running"),
    ("4", "Handshake Capture",       CMD_START_HANDSHAKE,         "handshake_running"),
    ("5", "Handshake → Serial PCAP", CMD_START_HANDSHAKE_SERIAL,  "handshake_running"),
    ("6", "Captive Portal",          None,                        "portal_running"),
    ("7", "Evil Twin",               None,                        "evil_twin_running"),
]


class AttackItem(urwid.WidgetWrap):
    """Single attack list item."""

    def __init__(self, key: str, label: str, active: bool) -> None:
        if active:
            text = urwid.Text(("attack_active", f"  [{key}] {label}  [RUNNING]"))
        else:
            text = urwid.Text(("default", f"  [{key}] {label}"))
        widget = urwid.AttrMap(text, None, focus_map="table_row_sel")
        super().__init__(widget)

    def selectable(self) -> bool:
        return True

    def keypress(self, size, key):
        return key


class AttacksScreen(urwid.WidgetWrap):
    """Attack list — number keys start attacks (with confirm), 9 stops all.
    Includes a live serial log showing ESP32 feedback during attacks."""

    def __init__(self, state: AppState, serial: SerialManager, app,
                 loot: LootManager | None = None,
                 portal=None, evil_twin=None) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot
        self._portal = portal
        self._evil_twin = evil_twin
        self._sub_screen = None  # active sub-screen (Portal/EvilTwin) or None

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [1-7]Start  [9]Stop all  [x]Clear  [Esc]Back"))
        self._last_flags = ""  # track state changes to avoid needless rebuilds

        log_label = urwid.AttrMap(
            urwid.Text(("dim", "  ── ESP32 Output ──")), "default"
        )

        self._menu_view = urwid.Pile([
            ("fixed", 8, self._listbox),
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

        # Only rebuild when attack flags actually change
        flags = self._get_flags_key()
        if flags != self._last_flags:
            self._last_flags = flags
            self._rebuild()

        sel = self.state.selected_networks
        # Deduplicate running labels (handshake modes share same flag)
        seen = set()
        running = []
        for _, label, _, flag in ATTACKS:
            if getattr(self.state, flag, False) and flag not in seen:
                seen.add(flag)
                running.append(label)
        if running:
            run_str = ", ".join(running)
            self._status.set_text(
                ("attack_active", f"  ACTIVE: {run_str} | [9]Stop all  [x]Clear log")
            )
        elif sel:
            self._status.set_text(("dim", f"  Target: {sel} | [1-7]Start  [9]Stop all  [x]Clear log"))
        else:
            self._status.set_text(("warning", "  No networks selected! Go to Scan tab first"))

    def _get_flags_key(self) -> str:
        return ",".join(
            str(getattr(self.state, flag, False)) for _, _, _, flag in ATTACKS
        )

    def _rebuild(self) -> None:
        _, old_focus = self._listbox.get_focus()
        self._walker.clear()
        for key, label, cmd, flag in ATTACKS:
            active = getattr(self.state, flag, False)
            self._walker.append(AttackItem(key, label, active))
        if old_focus is not None and self._walker:
            self._listbox.set_focus(min(old_focus, len(self._walker) - 1))

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
    # Attack start
    # ------------------------------------------------------------------

    def _start_attack(self, idx: int) -> None:
        if idx >= len(ATTACKS):
            return
        key, label, cmd, flag = ATTACKS[idx]

        # Portal → sub-screen
        if cmd is None and flag == "portal_running" and self._portal:
            self._enter_sub_screen(self._portal)
            return

        # Evil Twin → sub-screen
        if cmd is None and flag == "evil_twin_running" and self._evil_twin:
            self._enter_sub_screen(self._evil_twin)
            return

        # Handshake Serial mode attacks all visible networks — no selection needed
        serial_mode = (cmd == CMD_START_HANDSHAKE_SERIAL)
        if not serial_mode and not self.state.selected_networks:
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
                if serial_mode:
                    self._log.append(
                        ">>> Handshake Serial started — PCAP will be saved to loot/handshakes/ automatically",
                        "attack_active"
                    )
                else:
                    self._log.append(f">>> {label} started — waiting for ESP32 output...", "attack_active")
                self._last_flags = ""  # force rebuild
                if self._loot:
                    targets = self.state.selected_networks if not serial_mode else "all"
                    self._loot.log_attack_event(f"STARTED: {label} (targets: {targets})")
            else:
                self._status.set_text(("dim", f"  {label} cancelled"))

        confirm_msg = (
            f"Start {label}?\nWill attack all visible networks.\nPCAP saved to loot/handshakes/"
            if serial_mode else f"Start {label}?"
        )
        dialog = ConfirmDialog(confirm_msg, on_confirm)
        self._app.show_overlay(dialog, 55, 10 if serial_mode else 8)

    def _stop_all(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.stop_all()
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

        # Attack menu mode
        if key in ("1", "2", "3", "4", "5", "6", "7"):
            self._start_attack(int(key) - 1)
            return None
        if key == "9":
            self._stop_all()
            return None
        if key == "x":
            self._log.clear()
            return None
        return super().keypress(size, key)
