"""Sniffer screen — live packet counter, AP results, probe requests."""

import time
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...config import (
    CMD_START_SNIFFER,
    CMD_STOP,
    CMD_SHOW_SNIFFER_RESULTS,
    CMD_SHOW_PROBES,
    CMD_CLEAR_SNIFFER_RESULTS,
)
from ..widgets.data_table import DataTable


class SnifferScreen(urwid.WidgetWrap):
    """Sniffer with three sub-views toggled by hotkeys:
    - [s] start/stop sniffer
    - [r] fetch & show AP results
    - [p] fetch & show probe requests
    - [x] clear results
    - [l] switch to live view
    """

    VIEW_LIVE = 0
    VIEW_RESULTS = 1
    VIEW_PROBES = 2

    def __init__(self, state: AppState, serial: SerialManager, net_mgr: NetworkManager) -> None:
        self.state = state
        self.serial = serial
        self.net_mgr = net_mgr
        self._view = self.VIEW_LIVE
        self._fetching_results = False
        self._fetching_probes = False
        self._fetch_lines: list[str] = []
        self._fetch_start: float = 0

        # Live counter view — ListBox so the widget is selectable (keys work)
        self._live_text = urwid.Text(("sniffer_live", "  Sniffer idle"), align="left")
        self._live_view = urwid.ListBox(urwid.SimpleFocusListWalker([self._live_text]))

        # Results table
        self._results_table = DataTable([
            ("weight", 2, urwid.Text("SSID")),
            ("fixed", 4,  urwid.Text("CH")),
            ("fixed", 8,  urwid.Text("Clients")),
        ])

        # Probes table
        self._probes_table = DataTable([
            ("weight", 2, urwid.Text("SSID")),
            ("fixed", 20, urwid.Text("MAC")),
        ])

        self._status = urwid.Text(("dim", "  [s]Start  [r]Results  [p]Probes  [x]Clear"))
        self._body = urwid.WidgetPlaceholder(self._live_view)

        pile = urwid.Pile([
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(pile)

    def refresh(self) -> None:
        if self._view == self.VIEW_LIVE:
            if self.state.sniffer_running:
                self._live_text.set_text(
                    ("sniffer_count",
                     f"  Sniffer RUNNING\n\n"
                     f"  Packets captured: {self.state.sniffer_packets}\n"
                     f"  Buffer lines: {len(self.state.sniffer_buffer)}\n\n"
                     f"  [r] AP results  [p] Probes  [s] Stop")
                )
            else:
                self._live_text.set_text(
                    ("sniffer_live", "  Sniffer idle\n\n  Press [s] to start")
                )

        # Timeout-based fetch completion (3 seconds)
        if self._fetching_results and self._fetch_start > 0:
            if time.time() - self._fetch_start > 3:
                self._finish_results()
        if self._fetching_probes and self._fetch_start > 0:
            if time.time() - self._fetch_start > 3:
                self._finish_probes()

        # Update status hints
        if self._fetching_results:
            self._status.set_text(("warning", f"  Fetching results... ({len(self._fetch_lines)} lines)"))
        elif self._fetching_probes:
            self._status.set_text(("warning", f"  Fetching probes... ({len(self._fetch_lines)} lines)"))
        elif self.state.sniffer_running:
            self._status.set_text(("dim", "  [s]Stop  [r]Results  [p]Probes  [x]Clear  [l]Live"))
        else:
            self._status.set_text(("dim", "  [s]Start  [r]Results(buf)  [p]Probes(buf)  [x]Clear"))

    def handle_serial_line(self, line: str) -> None:
        """Handle lines while sniffer tab is active."""
        if self._fetching_results:
            self._fetch_lines.append(line)
            if "printed" in line.lower():
                self._finish_results()
            return
        if self._fetching_probes:
            self._fetch_lines.append(line)
            if "printed" in line.lower():
                self._finish_probes()
            return

    def _start_sniffer(self) -> None:
        self.state.reset_sniffer()
        self.state.sniffer_running = True
        self.serial.send_command(CMD_START_SNIFFER)
        self._view = self.VIEW_LIVE
        self._body.original_widget = self._live_view
        self._status.set_text(("success", "  Sniffer started!"))

    def _stop_sniffer(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.sniffer_running = False
        self._status.set_text(("warning", "  Sniffer stopped. Use [r]/[p] to view buffered data."))

    def _fetch_results(self) -> None:
        self._fetching_results = True
        self._fetch_lines = []
        self._fetch_start = time.time()
        if self.state.sniffer_running:
            self.serial.send_command(CMD_SHOW_SNIFFER_RESULTS)
            self._status.set_text(("warning", "  Fetching AP results from device..."))
        else:
            # Use buffered data
            self._fetch_lines = list(self.state.sniffer_buffer)
            self._finish_results()

    def _finish_results(self) -> None:
        self._fetching_results = False
        self._fetch_start = 0
        self.net_mgr.parse_sniffer_results(self._fetch_lines)
        rows = []
        for ap in self.state.sniffer_aps:
            rows.append([
                ("weight", 2, urwid.Text(ap.ssid)),
                ("fixed", 4,  urwid.Text(str(ap.channel))),
                ("fixed", 8,  urwid.Text(str(ap.client_count))),
            ])
            for mac in ap.clients:
                rows.append([
                    ("weight", 2, urwid.Text(("dim", f"  {mac}"))),
                    ("fixed", 4,  urwid.Text("")),
                    ("fixed", 8,  urwid.Text("")),
                ])
        self._results_table.set_rows(rows)
        self._view = self.VIEW_RESULTS
        self._body.original_widget = self._results_table
        n = len(self.state.sniffer_aps)
        self._status.set_text(("success", f"  {n} APs found | [r]Refresh  [p]Probes  [l]Live"))

    def _fetch_probes(self) -> None:
        self._fetching_probes = True
        self._fetch_lines = []
        self._fetch_start = time.time()
        if self.state.sniffer_running:
            self.serial.send_command(CMD_SHOW_PROBES)
            self._status.set_text(("warning", "  Fetching probes from device..."))
        else:
            # Use buffered data
            self._fetch_lines = list(self.state.sniffer_buffer)
            self._finish_probes()

    def _finish_probes(self) -> None:
        self._fetching_probes = False
        self._fetch_start = 0
        self.net_mgr.parse_probes(self._fetch_lines)
        rows = []
        for p in self.state.sniffer_probes:
            rows.append([
                ("weight", 2, urwid.Text(p.ssid)),
                ("fixed", 20, urwid.Text(("dim", p.mac))),
            ])
        self._probes_table.set_rows(rows)
        self._view = self.VIEW_PROBES
        self._body.original_widget = self._probes_table
        n = len(self.state.sniffer_probes)
        self._status.set_text(("success", f"  {n} probes | [p]Refresh  [r]Results  [l]Live"))

    def _clear_results(self) -> None:
        self.serial.send_command(CMD_CLEAR_SNIFFER_RESULTS)
        self.state.sniffer_aps.clear()
        self.state.sniffer_probes.clear()
        self.state.sniffer_buffer.clear()
        self._results_table.clear()
        self._probes_table.clear()
        self._status.set_text(("dim", "  Results cleared"))

    def keypress(self, size, key):
        if key == "s":
            if self.state.sniffer_running:
                self._stop_sniffer()
            else:
                self._start_sniffer()
            return None
        if key == "r":
            self._fetch_results()
            return None
        if key == "p":
            self._fetch_probes()
            return None
        if key == "x":
            self._clear_results()
            return None
        if key == "l":
            self._view = self.VIEW_LIVE
            self._body.original_widget = self._live_view
            return None
        return super().keypress(size, key)
