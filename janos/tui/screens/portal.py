"""Portal screen — setup wizard + live monitoring via LogViewer."""

import base64
import logging
import os
import re
import time

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...privacy import mask_line, mask_ssid
from ...config import CMD_SET_HTML, CMD_START_PORTAL, CMD_STOP
from ..widgets.log_viewer import LogViewer
from ..widgets.text_input_dialog import TextInputDialog
from ..widgets.file_picker import FilePicker
from ..widgets.choice_dialog import ChoiceDialog
from ..widgets.confirm_dialog import ConfirmDialog

log = logging.getLogger(__name__)

# Base64 chunk size for set_html serial transfer (~512 chars per line)
_B64_CHUNK = 512


def _portals_dir() -> str:
    """Return absolute path to the portals/ directory (next to janos/ package).

    portal.py lives at janos/tui/screens/ — 4 levels up to reach app root.
    """
    # janos/tui/screens/portal.py → screens → tui → janos → JanOS-app/
    app_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(app_root, "portals")


class PortalScreen(urwid.WidgetWrap):
    """Multi-step portal setup wizard, then live log monitoring.

    Wizard flow:
      [s] → SSID input → built-in/custom/cancel choice
        → built-in: confirm & start with firmware default
        → custom:  pick HTML from portals/ folder → send via set_html → start

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

        # Wizard temp storage
        self._ssid = ""

    def refresh(self) -> None:
        if self.state.portal_running:
            self._info.set_text(
                ("success",
                 f"  Portal RUNNING | SSID: {mask_ssid(self.state.portal_ssid)}"
                 f" | Forms: {self.state.submitted_forms}"
                 f" | Clients: {self.state.portal_client_count}")
            )
            self._status.set_text(("dim", "  [x]Stop  [d]Show data"))
        else:
            self._info.set_text(("portal", "  Captive Portal — idle"))
            self._status.set_text(("dim", "  [s]Setup portal"))

    def handle_serial_line(self, line: str) -> None:
        """Process serial lines when portal tab is active."""
        if self.state.portal_running:
            self.state.portal_log.append(line)
            self._route_portal_event(line)
            self._log.append(mask_line(line), self._event_attr(line))

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
            if self._loot:
                self._loot.save_portal_event(line)
        elif any(kw in line.lower() for kw in ("form data:", "username:", "email:")):
            self.state.submitted_forms += 1
            self.state.last_submitted_data = line
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
            # Step 2: built-in / custom / cancel
            self._ask_html_choice()

        dialog = TextInputDialog("SSID name (e.g. Free WiFi)", on_ssid, "Free WiFi")
        self._app.show_overlay(dialog, 50, 8)

    def _ask_html_choice(self) -> None:
        """Show y/n/c dialog: use built-in HTML or pick a custom portal."""

        def on_choice(answer: str) -> None:
            self._app.dismiss_overlay()
            if answer == "y":
                self._confirm_start_default()
            elif answer == "n":
                self._show_local_portals()
            else:  # 'c' — cancel
                self._status.set_text(("dim", "  Setup cancelled"))

        dialog = ChoiceDialog("Use built-in portal HTML?", on_choice)
        self._app.show_overlay(dialog, 50, 8)

    # ------------------------------------------------------------------
    # Built-in HTML path
    # ------------------------------------------------------------------

    def _confirm_start_default(self) -> None:
        """Confirm start with firmware's built-in default portal HTML."""
        self.state.selected_html_name = "(default)"
        msg = f"Start portal?\nSSID: {self._ssid}\nHTML: built-in default"

        def on_confirm(yes):
            self._app.dismiss_overlay()
            if not yes:
                self._status.set_text(("dim", "  Portal start cancelled"))
                return
            self._do_start()

        dialog = ConfirmDialog(msg, on_confirm)
        self._app.show_overlay(dialog, 55, 9)

    # ------------------------------------------------------------------
    # Custom HTML path (local portals/ folder)
    # ------------------------------------------------------------------

    def _show_local_portals(self) -> None:
        """Scan portals/ folder and show file picker."""
        pdir = _portals_dir()
        try:
            files = sorted(
                f for f in os.listdir(pdir)
                if f.lower().endswith(".html")
            )
        except OSError:
            files = []

        if not files:
            self._status.set_text(
                ("error", f"  No .html files in portals/ folder")
            )
            return

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                self._status.set_text(("dim", "  File selection cancelled"))
                return
            filepath = os.path.join(pdir, name)
            self._send_custom_html(filepath, name)

        picker = FilePicker(files, on_pick)
        self._app.show_overlay(picker, 55, min(len(files) + 6, 20))

    def _send_custom_html(self, filepath: str, filename: str) -> None:
        """Read local HTML file and send to ESP32 via set_html (base64 chunked)."""
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                html_content = fh.read()
        except OSError as exc:
            self._status.set_text(("error", f"  Cannot read {filename}: {exc}"))
            return

        self.state.selected_html_name = filename

        # Base64 encode and send in chunks
        html_b64 = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
        self.serial.send_command("set_html_begin")
        for i in range(0, len(html_b64), _B64_CHUNK):
            chunk = html_b64[i : i + _B64_CHUNK]
            self.serial.send_command(chunk)
        self.serial.send_command("set_html_end")

        log.info("Sent custom HTML %s (%d bytes, %d b64 chars)", filename,
                 len(html_content), len(html_b64))
        self._status.set_text(("success", f"  Custom HTML sent: {filename}"))
        self._confirm_start()

    # ------------------------------------------------------------------
    # Confirm & start
    # ------------------------------------------------------------------

    def _confirm_start(self) -> None:
        msg = (
            f"Start portal?\n"
            f"SSID: {self._ssid}\n"
            f"HTML: {self.state.selected_html_name}"
        )

        def on_confirm(yes):
            self._app.dismiss_overlay()
            if not yes:
                self._status.set_text(("dim", "  Portal start cancelled"))
                return
            self._do_start()

        dialog = ConfirmDialog(msg, on_confirm)
        self._app.show_overlay(dialog, 55, 9)

    def _do_start(self) -> None:
        # Ensure ESP32 is idle — stop + wait for cleanup
        self.serial.send_command(CMD_STOP)
        self.state.stop_all()
        time.sleep(1.5)
        self.serial.read_available()  # drain stale data
        self.state.reset_portal()
        self.state.portal_ssid = self._ssid
        self.state.portal_running = True
        self._log.clear()
        self.serial.send_command(f"{CMD_START_PORTAL} {self._ssid}")
        self._body.original_widget = self._log
        self._log.append("Portal starting...", "warning")

    def _stop_portal(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.stop_all()
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
        self._body.original_widget = self._log
        pw_lines = [l for l in self.state.portal_log
                     if any(kw in l.lower() for kw in ("password:", "form data:", "username:", "email:"))]
        if pw_lines:
            self._log.append("=== Captured Data ===", "success")
            for pl in pw_lines:
                self._log.append(mask_line(pl), "success")
        else:
            self._log.append("No passwords/forms captured yet", "dim")
