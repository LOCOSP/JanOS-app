"""Add-ons screen — extra tools (firmware flash, etc.)."""

import queue
import time

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...flash_manager import FlashManager
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog

ADDONS = [
    ("1", "Flash ESP32-C5 Firmware"),
]


class _AddonItem(urwid.WidgetWrap):
    def __init__(self, key: str, label: str) -> None:
        text = urwid.Text(("default", f"  [{key}] {label}"))
        super().__init__(text)


class AddOnsScreen(urwid.WidgetWrap):
    """Add-ons menu with firmware flashing and live log output."""

    def __init__(self, state: AppState, serial: SerialManager, app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._flash = FlashManager()
        self._flashing = False
        self._reconnect_pending = False
        self._reconnect_at: float = 0.0

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._log = LogViewer(max_lines=500)
        self._status = urwid.Text(("dim", "  [1] Flash Firmware"))

        log_label = urwid.AttrMap(
            urwid.Text(("dim", "  ── Output ──")), "default",
        )

        self._view = urwid.Pile([
            ("fixed", len(ADDONS) + 1, self._listbox),
            ("pack", log_label),
            self._log,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._view)
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        self._walker.clear()
        for key, label in ADDONS:
            self._walker.append(_AddonItem(key, label))

    # ------------------------------------------------------------------
    # Refresh (called every 1s by app._tick)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # Drain flash output queue
        drained = 0
        while not self._flash.queue.empty():
            try:
                line, attr = self._flash.queue.get_nowait()
                self._log.append(line, attr)
                drained += 1
            except queue.Empty:
                break

        if self._flash.running:
            self._status.set_text(
                ("attack_active", "  FLASHING... Please wait"),
            )
            return

        # Flash just finished
        if self._flash.done and not self._reconnect_pending:
            self._flash.done = False
            self._flashing = False
            if self._flash.success:
                self._reconnect_pending = True
                self._reconnect_at = time.time() + 3  # wait for ESP32 boot
                self._status.set_text(
                    ("success", "  Flash OK! Reconnecting serial in 3s..."),
                )
            else:
                self._status.set_text(
                    ("error", "  Flash FAILED — check log above  [x] Clear"),
                )

        # Deferred serial reconnect
        if self._reconnect_pending and time.time() >= self._reconnect_at:
            self._reconnect_pending = False
            self._reconnect_serial()

    # ------------------------------------------------------------------
    # Flash firmware
    # ------------------------------------------------------------------

    def _start_flash(self) -> None:
        if self._flashing:
            self._status.set_text(("warning", "  Flash already in progress"))
            return

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self._begin_flash()

        dialog = ConfirmDialog(
            "Flash ESP32-C5 with latest firmware?\n"
            "Serial will disconnect during flash.\n"
            "Hold BOOT + replug USB when prompted.",
            on_confirm,
        )
        self._app.show_overlay(dialog, 50, 10)

    def _begin_flash(self, erase: bool = False) -> None:
        self._flashing = True
        self._log.clear()
        self._log.append("Starting firmware flash...", "attack_active")

        # Close serial so esptool can use the port
        if self.state.connected:
            try:
                self._app._loop.remove_watch_file(self.serial.fd)
            except Exception:
                pass
            self.serial.close()
            self.state.connected = False
            self._log.append("Serial port closed.", "dim")
            self._log.append("", "default")

        self._flash.start(erase=erase)

    # ------------------------------------------------------------------
    # Serial reconnect (after flash)
    # ------------------------------------------------------------------

    def _reconnect_serial(self) -> None:
        self._log.append("Reconnecting serial port...", "dim")
        try:
            self.serial.setup()
            self.state.connected = True
            self._app._loop.watch_file(
                self.serial.fd, self._app._on_serial_data,
            )
            self._log.append(
                f"Serial reconnected: {self.serial.device}", "success",
            )
            self._status.set_text(
                ("success", "  Flash complete! Serial OK.  [x] Clear"),
            )
        except Exception as exc:
            self._log.append(f"Reconnect failed: {exc}", "warning")
            self._log.append("Restart JanOS or replug device.", "warning")
            self._status.set_text(
                ("warning", "  Flash done, serial not reconnected.  [x] Clear"),
            )

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        if key == "1" and not self._flashing:
            self._start_flash()
            return None
        if key == "x":
            if not self._flashing:
                self._log.clear()
                self._status.set_text(("dim", "  [1] Flash Firmware"))
            return None
        return super().keypress(size, key)
