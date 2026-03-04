"""Evil Twin screen — target selection, setup wizard, live monitoring."""

import re
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...loot_manager import LootManager
from ...privacy import mask_line, mask_ssid
from ...config import (
    CMD_LIST_SD,
    CMD_SELECT_HTML,
    CMD_SELECT_NETWORKS,
    CMD_START_EVIL_TWIN,
    CMD_STOP,
)
from ..widgets.log_viewer import LogViewer
from ..widgets.file_picker import FilePicker
from ..widgets.confirm_dialog import ConfirmDialog


class EvilTwinScreen(urwid.WidgetWrap):
    """Evil Twin attack: select target network -> select HTML -> start.

    Hotkeys:
    - [s] start setup wizard
    - [x/Esc] stop attack
    """

    def __init__(self, state: AppState, serial: SerialManager, net_mgr: NetworkManager, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self.serial = serial
        self.net_mgr = net_mgr
        self._app = app
        self._loot = loot

        self._log = LogViewer()
        self._status = urwid.Text(("dim", "  [s]Setup evil twin"))
        self._info = urwid.Text(("evil_twin", "  Evil Twin — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("evil_twin", "  Press [s] to start evil twin setup\n"
                                      "  Requires scanned networks (Scan tab)")),
        ]))
        self._body = urwid.WidgetPlaceholder(self._idle_view)

        pile = urwid.Pile([
            ("pack", self._info),
            ("pack", urwid.Divider("─")),
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(pile)

        self._target_ssid = ""
        self._target_channel = ""
        self._html_files: list[dict] = []
        self._fetch_lines: list[str] = []
        self._fetching_files = False
        self._file_load_timeout = None

    def refresh(self) -> None:
        if self.state.evil_twin_running:
            self._info.set_text(
                ("attack_active",
                 f"  Evil Twin RUNNING | Target: {mask_ssid(self.state.evil_twin_ssid)}"
                 f" | Captured: {len(self.state.evil_twin_captured_data)}"
                 f" | Clients: {self.state.evil_twin_client_count}")
            )
            self._status.set_text(("dim", "  [x]Stop  [d]Show data"))
        else:
            self._info.set_text(("evil_twin", "  Evil Twin — idle"))
            self._status.set_text(("dim", "  [s]Setup evil twin"))

    def handle_serial_line(self, line: str) -> None:
        if self._fetching_files:
            self._fetch_lines.append(line)
            if "No HTML" in line or "files found" in line.lower() or len(self._fetch_lines) > 100:
                self._finish_file_load()
            return

        if self.state.evil_twin_running:
            self.state.evil_twin_log.append(line)
            self._route_event(line)
            self._log.append(mask_line(line), self._event_attr(line))

    def _route_event(self, line: str) -> None:
        if "Client connected" in line:
            self.state.evil_twin_client_count += 1
        elif "trying to connect" in line.lower() or "association" in line.lower():
            pass  # logged
        elif "Password:" in line or "Handshake captured" in line:
            self.state.evil_twin_captured_data.append(line)
            # Save to loot
            if self._loot:
                self._loot.save_evil_twin_event(line)

    @staticmethod
    def _event_attr(line: str) -> str:
        ll = line.lower()
        if "error" in ll or "failed" in ll:
            return "error"
        if "password:" in ll or "handshake" in ll:
            return "success"
        if "client" in ll:
            return "evil_twin"
        return "default"

    # ------------------------------------------------------------------
    # Wizard
    # ------------------------------------------------------------------

    def _start_wizard(self) -> None:
        if self.state.evil_twin_running:
            self._status.set_text(("warning", "  Attack already running — stop first"))
            return
        if not self.state.networks:
            self._status.set_text(("error", "  No networks scanned — go to Scan tab first"))
            return

        # Step 1: pick target network from scan results
        names = [f"{n.ssid} (CH{n.channel} {n.rssi}dBm)" for n in self.state.networks]

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                self._status.set_text(("dim", "  Target selection cancelled"))
                return
            net = self.state.networks[idx]
            self._target_ssid = net.ssid
            self._target_channel = net.channel
            # Select that network on the ESP32
            self.serial.send_command(f"{CMD_SELECT_NETWORKS} {net.index}")
            self._load_html_files()

        picker = FilePicker(names, on_pick)
        self._app.show_overlay(picker, 55, min(len(names) + 6, 20))

    def _load_html_files(self) -> None:
        self._fetching_files = True
        self._fetch_lines = []
        self._status.set_text(("warning", "  Checking SD card for HTML files..."))
        self.serial.send_command(CMD_LIST_SD)
        # Timeout: if no response in 3 seconds, assume no SD card
        loop = self._app._loop
        if loop:
            self._file_load_timeout = loop.set_alarm_in(
                3.0, lambda *_: self._sd_timeout()
            )

    def _sd_timeout(self) -> None:
        """Called if SD card doesn't respond — use default HTML."""
        if not self._fetching_files:
            return
        self._fetching_files = False
        self._file_load_timeout = None
        self._confirm_start_default()

    def _finish_file_load(self) -> None:
        self._fetching_files = False
        # Cancel timeout if pending
        if self._file_load_timeout and self._app._loop:
            self._app._loop.remove_alarm(self._file_load_timeout)
            self._file_load_timeout = None

        self._html_files = []
        for line in self._fetch_lines:
            if re.search(r"^\s*\d+\s+\S+\.html\s*$", line):
                parts = line.strip().split()
                if len(parts) >= 2:
                    self._html_files.append({"number": parts[0], "name": parts[1]})

        if not self._html_files:
            # No SD card or no HTML files — use firmware default
            self._confirm_start_default()
            return

        names = [f["name"] for f in self._html_files]

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                self._status.set_text(("dim", "  File selection cancelled"))
                return
            file_info = self._html_files[idx]
            self.state.selected_html_name = file_info["name"]
            self.state.selected_html_index = int(file_info["number"])
            self.serial.send_command(f"{CMD_SELECT_HTML} {file_info['number']}")
            self._confirm_start()

        picker = FilePicker(names, on_pick)
        self._app.show_overlay(picker, 50, min(len(names) + 6, 20))

    def _confirm_start_default(self) -> None:
        """Confirm start with firmware's built-in default portal HTML."""
        self.state.selected_html_name = "(default)"
        msg = f"Start Evil Twin? Target={self._target_ssid} (using built-in HTML)"

        def on_confirm(yes):
            self._app.dismiss_overlay()
            if not yes:
                self._status.set_text(("dim", "  Evil Twin cancelled"))
                return
            self._do_start()

        dialog = ConfirmDialog(msg, on_confirm)
        self._app.show_overlay(dialog, 60, 8)

    def _confirm_start(self) -> None:
        msg = f"Start Evil Twin? Target={self._target_ssid} HTML={self.state.selected_html_name}"

        def on_confirm(yes):
            self._app.dismiss_overlay()
            if not yes:
                self._status.set_text(("dim", "  Evil Twin cancelled"))
                return
            self._do_start()

        dialog = ConfirmDialog(msg, on_confirm)
        self._app.show_overlay(dialog, 60, 8)

    def _do_start(self) -> None:
        self.state.reset_evil_twin()
        self.state.evil_twin_ssid = self._target_ssid
        self.state.evil_twin_running = True
        self._log.clear()
        self.serial.send_command(CMD_START_EVIL_TWIN)
        self._body.original_widget = self._log
        self._log.append("Evil Twin starting...", "warning")

    def _stop_attack(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.evil_twin_running = False
        self._log.append("Evil Twin stopped.", "warning")
        n = len(self.state.evil_twin_captured_data)
        self._status.set_text(("dim", f"  Stopped. Captured: {n}"))

    def _show_captured(self) -> None:
        if not self.state.evil_twin_captured_data:
            self._status.set_text(("dim", "  No captured data yet"))
            return
        # Show data from local log (show_pass requires SD card)
        self._body.original_widget = self._log
        self._log.append("=== Captured Data ===", "success")
        for line in self.state.evil_twin_captured_data:
            self._log.append(mask_line(line), "success")

    def keypress(self, size, key):
        if key == "s" and not self.state.evil_twin_running:
            self._start_wizard()
            return None
        if key in ("x", "esc") and self.state.evil_twin_running:
            self._stop_attack()
            return None
        if key == "d":
            self._show_captured()
            return None
        return super().keypress(size, key)
