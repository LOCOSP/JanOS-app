"""Attacks screen — start/stop attack types with confirmation."""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...config import (
    CMD_START_DEAUTH,
    CMD_START_BLACKOUT,
    CMD_SAE_OVERFLOW,
    CMD_START_HANDSHAKE,
    CMD_STOP,
)
from ..widgets.confirm_dialog import ConfirmDialog


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
    """Attack list — number keys start attacks (with confirm), 9 stops all."""

    def __init__(self, state: AppState, serial: SerialManager, app) -> None:
        self.state = state
        self.serial = serial
        self._app = app  # reference to JanOSTUI for overlay management

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._status = urwid.Text(("dim", "  [1-4]Start  [9]Stop all"))
        self._overlay_active = False

        self._container = urwid.Pile([
            self._listbox,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._container)
        self._rebuild()

    def refresh(self) -> None:
        self._rebuild()
        sel = self.state.selected_networks
        if sel:
            self._status.set_text(("dim", f"  Target: {sel} | [1-4]Start  [9]Stop all"))
        else:
            self._status.set_text(("warning", "  No networks selected! Go to Scan tab first"))

    def _rebuild(self) -> None:
        self._walker.clear()
        for key, label, cmd, flag in ATTACKS:
            active = getattr(self.state, flag, False)
            self._walker.append(AttackItem(key, label, active))

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

        # Show confirm dialog via overlay
        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self.serial.send_command(cmd)
                setattr(self.state, flag, True)
                self._status.set_text(("attack_active", f"  {label} STARTED"))
                self._rebuild()
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
        self._status.set_text(("success", "  All attacks stopped"))
        self._rebuild()

    def keypress(self, size, key):
        if key in ("1", "2", "3", "4"):
            self._start_attack(int(key) - 1)
            return None
        if key == "9":
            self._stop_all()
            return None
        return super().keypress(size, key)
