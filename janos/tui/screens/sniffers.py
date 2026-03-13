"""Sniffers tab — menu wrapper for Wardriving, BT Wardriving, and Packet Sniffer."""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...loot_manager import LootManager
from .sniffer import SnifferScreen
from .wardriving import WardrivingScreen
from .bt_wardriving import BTWardrivingScreen


class SniffersScreen(urwid.WidgetWrap):
    """Menu with sub-screen switching (same pattern as AttacksScreen)."""

    def __init__(self, state: AppState, serial: SerialManager,
                 net_mgr: NetworkManager, loot: LootManager | None,
                 app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._sub_screen = None  # active sub-screen or None (menu)

        # Sub-screens
        self._wardriving = WardrivingScreen(state, serial, net_mgr, loot, app)
        self._bt_wardriving = BTWardrivingScreen(state, serial, loot, app)
        self._sniffer = SnifferScreen(state, serial, net_mgr, loot)

        # Menu view
        self._menu_items = urwid.Pile([
            urwid.Text(("bold", "  \u2500\u2500 Sniffers \u2500\u2500")),
            urwid.Divider(),
            urwid.Text(("default", "  [1] Wardriving WiFi")),
            urwid.Text(("default", "  [2] Wardriving BT")),
            urwid.Text(("default", "  [3] Packet Sniffer")),
            urwid.Divider(),
        ])
        self._status = urwid.Text(("dim", "  Select mode"))
        self._menu_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            self._menu_items,
            self._status,
        ]))

        self._body = urwid.WidgetPlaceholder(self._menu_view)
        super().__init__(self._body)

    # ------------------------------------------------------------------
    # Sub-screen management
    # ------------------------------------------------------------------

    def _enter_sub_screen(self, screen) -> None:
        self._sub_screen = screen
        self._body.original_widget = screen

    def _exit_sub_screen(self) -> None:
        self._sub_screen = None
        self._body.original_widget = self._menu_view

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "refresh"):
                self._sub_screen.refresh()
            return
        # Menu view — update status hints
        parts = []
        if self.state.wardriving_running:
            n = self.state.wardriving_networks
            parts.append(f"WiFi WD RUNNING ({n} networks)")
        if self.state.bt_wardriving_running:
            n = self.state.bt_wardriving_devices
            parts.append(f"BT WD RUNNING ({n} devices)")
        if self.state.sniffer_running:
            parts.append(f"Sniffer RUNNING ({self.state.sniffer_packets} pkts)")
        if parts:
            self._status.set_text(("success", "  " + "  |  ".join(parts)))
        else:
            self._status.set_text(("dim", "  Select mode"))

    def handle_serial_line(self, line: str) -> None:
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "handle_serial_line"):
                self._sub_screen.handle_serial_line(line)
            return
        # No sub-screen active — still buffer sniffer packets
        # (existing behaviour: sniffer always counts packets)

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        # Sub-screen active
        if self._sub_screen is not None:
            if key == "esc":
                self._exit_sub_screen()
                return None
            return self._sub_screen.keypress(size, key)

        # Menu
        if key == "1":
            self._enter_sub_screen(self._wardriving)
            return None
        if key == "2":
            self._enter_sub_screen(self._bt_wardriving)
            return None
        if key == "3":
            self._enter_sub_screen(self._sniffer)
            return None
        return super().keypress(size, key)
