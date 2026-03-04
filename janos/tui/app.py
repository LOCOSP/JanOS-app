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
from ..config import CRASH_KEYWORDS
from .palette import PALETTE
from .header import HeaderWidget
from .footer import StatusBar
from .tabs import TabBar
from .screens.scan import ScanScreen
from .screens.sniffer import SnifferScreen
from .screens.attacks import AttacksScreen
from .screens.portal import PortalScreen
from .screens.evil_twin import EvilTwinScreen

log = logging.getLogger(__name__)

TAB_LABELS = ["Scan", "Sniffer", "Attacks", "Portal", "EvilTwin"]


class JanOSTUI:
    """Top-level TUI controller."""

    def __init__(self, device: str) -> None:
        # State & managers
        self.state = AppState(device=device, start_time=time.time())
        self.serial = SerialManager(device)
        self.net_mgr = NetworkManager(self.state)

        # Loot manager — save captured data to disk
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.loot = LootManager(app_dir)

        # Connect serial
        try:
            self.serial.setup()
            self.state.connected = True
        except Exception as exc:
            log.error("Serial setup failed: %s", exc)
            self.state.connected = False

        # Real screens
        self._scan = ScanScreen(self.state, self.serial, self.net_mgr, self.loot)
        self._sniffer = SnifferScreen(self.state, self.serial, self.net_mgr, self.loot)
        self._attacks = AttacksScreen(self.state, self.serial, self, self.loot)
        self._portal = PortalScreen(self.state, self.serial, self, self.loot)
        self._evil_twin = EvilTwinScreen(self.state, self.serial, self.net_mgr, self, self.loot)

        self._screens: list = [
            self._scan,
            self._sniffer,
            self._attacks,
            self._portal,
            self._evil_twin,
        ]

        # Widgets
        self._header = HeaderWidget(self.state)
        self._tab_bar = TabBar(TAB_LABELS, on_switch=self._on_tab_switch)
        self._footer = StatusBar(
            self.state,
            loot_path=self.loot.session_path if self.loot.active else "",
        )
        self._body = urwid.WidgetPlaceholder(self._screens[0])

        # Layout: header + tab_bar + body + footer
        top = urwid.Pile([
            ("pack", self._header),
            ("pack", self._tab_bar),
        ])
        frame = urwid.Frame(
            body=self._body,
            header=top,
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

        # 1-second refresh timer
        self._loop.set_alarm_in(1, self._tick)

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
    # Tab switching
    # ------------------------------------------------------------------

    def _on_tab_switch(self, index: int) -> None:
        self._body.original_widget = self._screens[index]

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

        for line in lines:
            log.debug("RX: %s", line)
            # Log every serial line to loot
            self.loot.log_serial(line)
            # Crash detection
            if self.serial.is_crash_line(line):
                self.state.firmware_crashed = True
                self.state.crash_message = line
                log.warning("Firmware crash detected: %s", line)
                self._show_crash_overlay(line)
                continue
            self._dispatch_line(line)
        if lines:
            self._refresh_ui()

    def _show_crash_overlay(self, message: str) -> None:
        """Show a red crash alert overlay."""
        text = urwid.Text(
            ("crash",
             f"\n  FIRMWARE CRASH DETECTED\n\n"
             f"  {message[:60]}\n\n"
             f"  ESP32 is rebooting.\n"
             f"  Press any key to dismiss.\n"),
            align="left",
        )
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="CRASH")
        widget = urwid.AttrMap(box, "crash")
        self.show_overlay(widget, 65, 10)
        # Reset all running states
        self.state.sniffer_running = False
        self.state.attack_running = False
        self.state.blackout_running = False
        self.state.sae_overflow_running = False
        self.state.handshake_running = False
        self.state.portal_running = False
        self.state.evil_twin_running = False
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
            self._quit()
            return True
        if key == "tab":
            self._tab_bar.next_tab()
            return True
        if key == "shift tab":
            self._tab_bar.prev_tab()
            return True
        # Number keys switch tabs (1-5)
        if key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if idx < len(self._screens):
                self._tab_bar.active = idx
            return True
        # Stop all
        if key == "9":
            self.serial.send_command("stop")
            self.state.attack_running = False
            self.state.blackout_running = False
            self.state.sae_overflow_running = False
            self.state.handshake_running = False
            self.state.sniffer_running = False
            self.state.portal_running = False
            self.state.evil_twin_running = False
            self._refresh_ui()
            return True
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _quit(self) -> None:
        if self.state.any_attack_running() or self.state.sniffer_running:
            self.serial.send_command("stop")
            time.sleep(0.2)
        self.loot.close()
        self.serial.close()
        raise urwid.ExitMainLoop()

    def run(self) -> None:
        self._loop.run()
