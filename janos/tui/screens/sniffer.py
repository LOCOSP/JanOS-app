"""Sniffer screen — live packet counter, AP results, probe requests."""

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

        # Live counter view
        self._live_text = urwid.Text(("sniffer_live", "  Sniffer idle"), align="left")
        self._live_view = urwid.Filler(self._live_text, valign="top")

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
                     f"  Press [r] to fetch AP results, [p] for probes")
                )
            else:
                self._live_text.set_text(
                    ("sniffer_live", "  Sniffer idle\n\n  Press [s] to start")
                )

        running = self.state.sniffer_running
        tag = "RUNNING" if running else "idle"
        hints = [f"  [{'' if running else 's]Start  '}"]
        if running:
            hints = ["  [s]Stop  "]
        else:
            hints = ["  [s]Start  "]
        hints.append("[r]Results  [p]Probes  [x]Clear")
        self._status.set_text(("dim", "".join(hints)))

    def handle_serial_line(self, line: str) -> None:
        """Handle lines while sniffer tab is active."""
        if self._fetching_results:
            self._fetch_lines.append(line)
            if "printed" in line.lower() or len(self._fetch_lines) > 500:
                self._finish_results()
            return
        if self._fetching_probes:
            self._fetch_lines.append(line)
            if "printed" in line.lower() or len(self._fetch_lines) > 500:
                self._finish_probes()
            return

    def _start_sniffer(self) -> None:
        self.state.reset_sniffer()
        self.state.sniffer_running = True
        self.serial.send_command(CMD_START_SNIFFER)
        self._view = self.VIEW_LIVE
        self._body.original_widget = self._live_view

    def _stop_sniffer(self) -> None:
        self.serial.send_command(CMD_STOP)
        self.state.sniffer_running = False

    def _fetch_results(self) -> None:
        if not self.state.sniffer_running:
            self._status.set_text(("warning", "  Start sniffer first before fetching results"))
            return
        self._fetching_results = True
        self._fetch_lines = []
        self.serial.send_command(CMD_SHOW_SNIFFER_RESULTS)

    def _finish_results(self) -> None:
        self._fetching_results = False
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
        self._status.set_text(("success", f"  {n} APs found | [s]Stop  [r]Refresh  [p]Probes"))

    def _fetch_probes(self) -> None:
        if not self.state.sniffer_running:
            self._status.set_text(("warning", "  Start sniffer first before fetching probes"))
            return
        self._fetching_probes = True
        self._fetch_lines = []
        self.serial.send_command(CMD_SHOW_PROBES)

    def _finish_probes(self) -> None:
        self._fetching_probes = False
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
        self._status.set_text(("success", f"  {n} probes | [s]Stop  [r]Results  [p]Refresh"))

    def _clear_results(self) -> None:
        self.serial.send_command(CMD_CLEAR_SNIFFER_RESULTS)
        self.state.sniffer_aps.clear()
        self.state.sniffer_probes.clear()
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
            # Switch to live view
            self._view = self.VIEW_LIVE
            self._body.original_widget = self._live_view
            return None
        return super().keypress(size, key)
