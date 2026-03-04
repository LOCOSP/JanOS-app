"""Attacks screen — start/stop attack types with confirmation + live log."""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...config import (
    CMD_START_DEAUTH,
    CMD_START_BLACKOUT,
    CMD_SAE_OVERFLOW,
    CMD_START_HANDSHAKE,
    CMD_STOP,
)
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.log_viewer import LogViewer


ATTACKS = [
    ("1", "Deauth Attack",       CMD_START_DEAUTH,    "attack_running"),
    ("2", "Blackout Attack",     CMD_START_BLACKOUT,   "blackout_running"),
    ("3", "WPA3 SAE Overflow",   CMD_SAE_OVERFLOW,     "sae_overflow_running"),
    ("4", "Handshake Capture",   CMD_START_HANDSHAKE,  "handshake_running"),
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
                 loot: LootManager | None = None) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [1-4]Start  [9]Stop all  [x]Clear log"))
        self._last_flags = ""  # track state changes to avoid needless rebuilds

        log_label = urwid.AttrMap(
            urwid.Text(("dim", "  ── ESP32 Output ──")), "default"
        )

        self._container = urwid.Pile([
            ("fixed", 6, self._listbox),
            ("pack", log_label),
            self._log,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._container)
        self._rebuild()

    def refresh(self) -> None:
        # Only rebuild when attack flags actually change
        flags = self._get_flags_key()
        if flags != self._last_flags:
            self._last_flags = flags
            self._rebuild()

        sel = self.state.selected_networks
        running = [label for _, label, _, flag in ATTACKS
                   if getattr(self.state, flag, False)]
        if running:
            run_str = ", ".join(running)
            self._status.set_text(
                ("attack_active", f"  ACTIVE: {run_str} | [9]Stop all  [x]Clear log")
            )
        elif sel:
            self._status.set_text(("dim", f"  Target: {sel} | [1-4]Start  [9]Stop all  [x]Clear log"))
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
        """Display serial output in the log viewer during active attacks."""
        if not self.state.any_attack_running():
            return
        # Color-code output
        line_lower = line.lower()
        if "error" in line_lower or "fail" in line_lower:
            attr = "error"
        elif "deauth" in line_lower or "handshake" in line_lower or "capture" in line_lower:
            attr = "attack_active"
        elif "sent" in line_lower or "ok" in line_lower or "success" in line_lower:
            attr = "success"
        else:
            attr = "dim"
        self._log.append(line.strip(), attr)

    def _start_attack(self, idx: int) -> None:
        if idx >= len(ATTACKS):
            return
        key, label, cmd, flag = ATTACKS[idx]

        if not self.state.selected_networks:
            self._status.set_text(("error", "  Select networks first (Scan tab)"))
            return

        if getattr(self.state, flag, False):
            self._status.set_text(("warning", f"  {label} already running"))
            return

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self.serial.send_command(cmd)
                setattr(self.state, flag, True)
                self._status.set_text(("attack_active", f"  {label} STARTED"))
                self._log.append(f">>> {label} started — waiting for ESP32 output...", "attack_active")
                self._last_flags = ""  # force rebuild
                if self._loot:
                    self._loot.log_attack_event(f"STARTED: {label} (targets: {self.state.selected_networks})")
            else:
                self._status.set_text(("dim", f"  {label} cancelled"))

        dialog = ConfirmDialog(f"Start {label}?", on_confirm)
        self._app.show_overlay(dialog, 40, 8)

    def _stop_all(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.attack_running = False
        self.state.blackout_running = False
        self.state.sae_overflow_running = False
        self.state.handshake_running = False
        self._log.append(">>> All attacks STOPPED", "warning")
        self._status.set_text(("success", "  All attacks stopped"))
        self._last_flags = ""  # force rebuild
        if self._loot:
            self._loot.log_attack_event("STOPPED: All attacks")

    def keypress(self, size, key):
        if key in ("1", "2", "3", "4"):
            self._start_attack(int(key) - 1)
            return None
        if key == "9":
            self._stop_all()
            return None
        if key == "x":
            self._log.clear()
            return None
        return super().keypress(size, key)
