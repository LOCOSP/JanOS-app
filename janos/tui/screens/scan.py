"""Scan screen — trigger scan, browse results, select networks."""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...config import CMD_SCAN_NETWORKS, CMD_SELECT_NETWORKS, CMD_UNSELECT_NETWORKS
from ..widgets.network_table import NetworkTable


class ScanScreen(urwid.WidgetWrap):
    """Full scan workflow:
    - s : start scan
    - Space/Enter : toggle network selection (auto-sent to ESP32)
    - c : clear selection
    - u : unselect all on device
    """

    def __init__(self, state: AppState, serial: SerialManager, net_mgr: NetworkManager) -> None:
        self.state = state
        self.serial = serial
        self.net_mgr = net_mgr

        self._scanning = False
        self._last_net_count = 0
        self._table = NetworkTable()
        self._status = urwid.Text(("dim", "  Press [s] to scan networks"))
        self._hint = urwid.Text(
            ("dim", "  [s]Scan  [Space]Select  [c]Clear  [u]Unselect all")
        )

        pile = urwid.Pile([
            ("pack", urwid.AttrMap(self._status, "default")),
            ("pack", urwid.Divider("─")),
            self._table,
            ("pack", urwid.Divider("─")),
            ("pack", self._hint),
        ])
        super().__init__(pile)

    def refresh(self) -> None:
        """Called every second by the main app tick."""
        n = len(self.state.networks)
        if n != self._last_net_count and not self._scanning:
            self._last_net_count = n
            self._table.update(self.state.networks)

        if self._scanning:
            n = len(self.state.networks)
            self._status.set_text(("warning", f"  Scanning... {n} networks found so far"))
        elif self.state.scan_done:
            n = len(self.state.networks)
            sel = self._table.get_selected_indices()
            sel_str = f" | Selected: {','.join(sel)}" if sel else ""
            self._status.set_text(("success", f"  Scan complete: {n} networks{sel_str}"))
        else:
            self._status.set_text(("dim", "  Press [s] to scan networks"))

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.net_mgr.clear()
        self._last_net_count = 0
        self._table.clear_selection()
        self.serial.send_command(CMD_SCAN_NETWORKS)
        self._status.set_text(("warning", "  Scanning..."))

    def handle_serial_line(self, line: str) -> None:
        """Called by the main app's serial dispatcher when scan tab is active."""
        if not self._scanning:
            return
        if line.startswith('"'):
            self.net_mgr.add_network(line)
            self._table.update(self.state.networks)
            self._last_net_count = len(self.state.networks)
        if "Scan results printed" in line:
            self._scanning = False
            self.state.scan_done = True

    def _sync_selection(self) -> None:
        """Send current selection to ESP32 and update state."""
        indices = self._table.get_selected_indices()
        if indices:
            selection_str = ",".join(indices)
            self.serial.send_command(f"{CMD_SELECT_NETWORKS} {selection_str}")
            self.state.selected_networks = selection_str
            self._status.set_text(("success", f"  Selected: {selection_str}"))
        else:
            self.serial.send_command(CMD_UNSELECT_NETWORKS)
            self.state.selected_networks = ""
            self._status.set_text(("dim", "  Selection cleared"))

    def _clear_selection(self) -> None:
        self._table.clear_selection()
        self._table.update(self.state.networks)
        self.serial.send_command(CMD_UNSELECT_NETWORKS)
        self.state.selected_networks = ""
        self._status.set_text(("dim", "  Selection cleared"))

    def keypress(self, size, key):
        if key == "s":
            self._start_scan()
            return None
        if key in (" ", "enter"):
            # Let table handle the toggle, then auto-send to ESP32
            super().keypress(size, key)
            self._sync_selection()
            return None
        if key == "c":
            self._clear_selection()
            return None
        if key == "u":
            self._clear_selection()
            return None
        return super().keypress(size, key)
