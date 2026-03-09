"""Main TUI application — urwid.MainLoop, serial watcher, tab routing."""

import logging
import os
import re
import time

import urwid

from ..app_state import AppState
from ..serial_manager import SerialManager
from ..network_manager import NetworkManager
from ..loot_manager import LootManager
from ..gps_manager import GpsManager
from .. import privacy
from ..config import CRASH_KEYWORDS
from .palette import PALETTE
from .header import HeaderWidget
from .footer import StatusBar
from .tabs import TabBar
from .screens.home import SidebarPanel
from .screens.scan import ScanScreen
from .screens.sniffer import SnifferScreen
from .screens.attacks import AttacksScreen
from .screens.portal import PortalScreen
from .screens.evil_twin import EvilTwinScreen
from .screens.addons import AddOnsScreen
from .widgets.confirm_dialog import ConfirmDialog
from .widgets.startup_screen import StartupScreen, run_startup_checks

log = logging.getLogger(__name__)

TAB_LABELS = ["Scan", "Sniffer", "Attacks", "Add-ons"]


class _CrashDialog(urwid.WidgetWrap):
    """Selectable crash overlay — any key dismisses it."""

    def __init__(self, details: str, on_dismiss) -> None:
        self._on_dismiss = on_dismiss
        text = urwid.Text(
            ("crash",
             f"\n  FIRMWARE CRASH DETECTED\n\n"
             f"  {details}\n"
             f"  ESP32 is rebooting.\n"
             f"  Press any key to dismiss.\n"),
            align="left",
        )
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="CRASH")
        widget = urwid.AttrMap(box, "crash")
        super().__init__(widget)

    def keypress(self, size, key):
        self._on_dismiss()
        return None

    def selectable(self) -> bool:
        return True


class JanOSTUI:
    """Top-level TUI controller."""

    def __init__(self, device: str) -> None:
        # State & managers
        self.state = AppState(device=device, start_time=time.time())
        self.serial = SerialManager(device)
        self.net_mgr = NetworkManager(self.state)

        # GPS module — optional, graceful degradation
        self.gps = GpsManager()
        self.state.gps_available = self.gps.setup()

        # Loot manager — save captured data to disk
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.loot = LootManager(app_dir, gps_manager=self.gps)

        # Connect serial
        try:
            self.serial.setup()
            self.state.connected = True
        except Exception as exc:
            log.error("Serial setup failed: %s", exc)
            self.state.connected = False

        # Portal & Evil Twin are sub-screens of Attacks — create first
        self._portal = PortalScreen(self.state, self.serial, self, self.loot)
        self._evil_twin = EvilTwinScreen(self.state, self.serial, self.net_mgr, self, self.loot)

        # Main screens
        self._scan = ScanScreen(self.state, self.serial, self.net_mgr, self.loot)
        self._sniffer = SnifferScreen(self.state, self.serial, self.net_mgr, self.loot)
        self._attacks = AttacksScreen(
            self.state, self.serial, self, self.loot,
            portal=self._portal, evil_twin=self._evil_twin,
        )

        # Add-ons screen
        self._addons = AddOnsScreen(self.state, self.serial, self)

        self._screens: list = [
            self._scan,
            self._sniffer,
            self._attacks,
            self._addons,
        ]

        # Sidebar — always-visible left panel with logo + stats
        self._sidebar = SidebarPanel(self.state, self.loot, gps=self.gps)
        self._mobile_mode = False

        # Widgets
        self._header = HeaderWidget(self.state)
        self._tab_bar = TabBar(TAB_LABELS, on_switch=self._on_tab_switch)
        self._footer = StatusBar(
            self.state,
            loot_path=self.loot.session_path if self.loot.active else "",
        )
        self._body = urwid.WidgetPlaceholder(self._screens[0])

        # Left column: tab bar + active screen
        self._left_panel = urwid.Frame(
            body=self._body,
            header=self._tab_bar,
        )

        # Main area: sidebar + right panel
        self._columns = urwid.Columns([
            ("weight", 35, self._sidebar),
            ("weight", 65, self._left_panel),
        ], dividechars=1, focus_column=1)

        # Layout: header + columns + footer
        self._content = urwid.WidgetPlaceholder(self._columns)
        frame = urwid.Frame(
            body=self._content,
            header=self._header,
            footer=self._footer,
        )
        self._frame = frame
        self._overlay_active = False

        # urwid main loop — wrap in Overlay-capable widget
        self._main_widget = urwid.WidgetPlaceholder(frame)
        self._loop = urwid.MainLoop(
            self._main_widget,
            palette=PALETTE,
            unhandled_input=self._unhandled_input,
        )

        # Serial FD watcher (if connected)
        if self.state.connected:
            self._loop.watch_file(self.serial.fd, self._on_serial_data)

        # GPS FD watcher (if available)
        if self.state.gps_available:
            self._loop.watch_file(self.gps.fd, self._on_gps_data)

        # 1-second refresh timer
        self._loop.set_alarm_in(1, self._tick)

        # Startup check dialog
        from ..config import GPS_DEVICE
        checks = run_startup_checks(
            device, self.state.connected, self.state.gps_available, GPS_DEVICE,
        )
        has_errors = any(c[0] == "fail" for c in checks)
        self._startup_screen = StartupScreen(checks, has_errors, on_dismiss=self._dismiss_startup)
        height = len(checks) + 6
        self.show_overlay(self._startup_screen, width=50, height=height)
        if not has_errors:
            self._loop.set_alarm_in(1, self._startup_screen.tick)

    def _dismiss_startup(self) -> None:
        self._startup_screen = None
        self.dismiss_overlay()

    # ------------------------------------------------------------------
    # Overlay support (for dialogs)
    # ------------------------------------------------------------------

    def show_overlay(self, widget: urwid.Widget, width: int, height: int) -> None:
        overlay = urwid.Overlay(
            widget,
            self._frame,
            align="center",
            valign="middle",
            width=width,
            height=height,
        )
        self._main_widget.original_widget = overlay
        self._overlay_active = True

    def dismiss_overlay(self) -> None:
        self._main_widget.original_widget = self._frame
        self._overlay_active = False

    # ------------------------------------------------------------------
    # Mobile mode toggle
    # ------------------------------------------------------------------

    def _toggle_mobile(self) -> None:
        self._mobile_mode = not self._mobile_mode
        if self._mobile_mode:
            # Mobile: full-width screen, no sidebar
            self._content.original_widget = self._left_panel
        else:
            # Desktop: sidebar on the right
            self._content.original_widget = self._columns
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def _on_tab_switch(self, index: int) -> None:
        self._body.original_widget = self._screens[index]

    # ------------------------------------------------------------------
    # GPS data callback (fired by urwid event loop)
    # ------------------------------------------------------------------

    def _on_gps_data(self) -> None:
        try:
            sentences = self.gps.read_available()
        except Exception:
            return
        if sentences:
            self.gps.process_sentences(sentences)
            fix = self.gps.fix
            self.state.gps_fix_valid = fix.valid
            self.state.gps_latitude = fix.latitude
            self.state.gps_longitude = fix.longitude
            self.state.gps_altitude = fix.altitude
            self.state.gps_satellites = fix.satellites
            self.state.gps_satellites_visible = fix.satellites_visible
            self.state.gps_fix_quality = fix.fix_quality
            self.state.gps_hdop = fix.hdop

    # ------------------------------------------------------------------
    # Serial data callback (fired by urwid event loop)
    # ------------------------------------------------------------------

    def _on_serial_data(self) -> None:
        try:
            lines = self.serial.read_available()
        except Exception as exc:
            log.error("Serial read error: %s", exc)
            self.state.connected = False
            self._refresh_ui()
            return

        crash_lines = []
        for line in lines:
            log.debug("RX: %s", line)
            # Log every serial line to loot
            self.loot.log_serial(line)
            # Crash detection — collect all crash lines, show ONE overlay
            if self.serial.is_crash_line(line):
                self.state.firmware_crashed = True
                self.state.crash_message = line
                log.warning("Firmware crash detected: %s", line)
                crash_lines.append(line)
                continue
            self._dispatch_line(line)
        if crash_lines:
            self._show_crash_overlay(crash_lines)
        if lines:
            self._refresh_ui()

    def _show_crash_overlay(self, crash_lines: list) -> None:
        """Show a red crash alert overlay (single, selectable)."""
        # Don't stack overlays — dismiss any existing one first
        if self._overlay_active:
            self.dismiss_overlay()
        summary = "\n  ".join(ln[:60] for ln in crash_lines[-4:])
        widget = _CrashDialog(summary, lambda: self.dismiss_overlay())
        self.show_overlay(widget, 65, 12)
        # Reset all running states
        self.state.stop_all()
        self._refresh_ui()

    def _dispatch_line(self, line: str) -> None:
        """Route an incoming serial line to the active screen's handler."""
        active_idx = self._tab_bar.active
        screen = self._screens[active_idx]

        # Always update sniffer count if sniffer is running
        if self.state.sniffer_running:
            self.state.sniffer_buffer.append(line)
            count = self.net_mgr.extract_packet_count(line)
            if count is not None:
                self.state.sniffer_packets = count
            elif re.search(r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}", line):
                self.state.sniffer_packets += 1

        # Route to the active screen
        if hasattr(screen, "handle_serial_line"):
            screen.handle_serial_line(line)

        # Also route to attacks screen when attacks are running (even from other tabs)
        if self.state.any_attack_running() and screen is not self._attacks:
            self._attacks.handle_serial_line(line)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def _tick(self, loop=None, data=None) -> None:
        self._refresh_ui()
        self._loop.set_alarm_in(1, self._tick)

    def _refresh_ui(self) -> None:
        self._header.refresh()
        self._footer.refresh()
        self._sidebar.refresh()
        current = self._screens[self._tab_bar.active]
        if hasattr(current, "refresh"):
            current.refresh()

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def _unhandled_input(self, key: str) -> bool:
        # If overlay is active, only let Esc dismiss it
        if self._overlay_active:
            if key == "esc":
                self.dismiss_overlay()
                return True
            return False

        if key in ("q", "Q"):
            self._confirm_quit()
            return True
        # Private mode toggle
        if key == "P":
            privacy.set_private_mode(not privacy.is_private())
            # Force full UI rebuild (tables need redrawing with masked data)
            self._scan._last_net_count = -1
            self._attacks._last_flags = ""
            self._refresh_ui()
            return True
        # Mobile mode toggle
        if key == "M":
            self._toggle_mobile()
            return True
        if key in ("tab", "right"):
            self._tab_bar.next_tab()
            return True
        if key in ("shift tab", "left"):
            self._tab_bar.prev_tab()
            return True
        # Number keys switch tabs (1-4)
        if key in ("1", "2", "3", "4"):
            idx = int(key) - 1
            if idx < len(self._screens):
                self._tab_bar.active = idx
            return True
        # Stop all
        if key == "9":
            self.serial.send_command("stop")
            self.state.stop_all()
            self._refresh_ui()
            return True
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _confirm_quit(self) -> None:
        def _on_answer(yes: bool) -> None:
            self.dismiss_overlay()
            if yes:
                self._quit()
        dialog = ConfirmDialog("Quit JanOS?", _on_answer)
        self.show_overlay(dialog, 35, 7)

    def _quit(self) -> None:
        # Always send stop — even if flags are out of sync with ESP32 state
        try:
            self.serial.send_command("stop")
            time.sleep(0.3)
            # Second stop for good measure (some modes need it)
            self.serial.send_command("stop")
            time.sleep(0.1)
        except Exception:
            pass
        self.loot.close()
        self.gps.close()
        self.serial.close()
        raise urwid.ExitMainLoop()

    def run(self) -> None:
        self._loop.run()
