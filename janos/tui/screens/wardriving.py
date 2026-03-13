"""Wardriving screen — continuous WiFi scan with GPS geo-tagging."""

import time
import urwid

from ...app_state import AppState, Network
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...loot_manager import LootManager
from ...privacy import mask_ssid, mask_mac, mask_coords_str, is_private
from ...config import CMD_SCAN_NETWORKS, CMD_STOP
from ..widgets.data_table import DataTable
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.info_dialog import InfoDialog


# Delay between scan cycles (seconds)
_SCAN_INTERVAL = 2.0


class WardrivingScreen(urwid.WidgetWrap):
    """Continuous WiFi scanning with GPS coordinates per network.

    Press [s] to start/stop.  Networks are de-duplicated by BSSID —
    only the strongest RSSI observation is kept.
    """

    def __init__(self, state: AppState, serial: SerialManager,
                 net_mgr: NetworkManager, loot: LootManager | None,
                 app) -> None:
        self.state = state
        self.serial = serial
        self.net_mgr = net_mgr
        self._loot = loot
        self._app = app

        # Wardriving state
        self._running = False
        self._waiting_gps = False
        self._scanning = False          # currently in a scan cycle
        self._scan_done_time: float = 0  # when last cycle finished
        self._cycle_count = 0
        # bssid -> Network (best RSSI)
        self._seen: dict[str, Network] = {}
        # Current cycle networks (parsed from serial)
        self._cycle_nets: list[Network] = []

        # UI
        self._table = DataTable([
            ("weight", 2, urwid.Text("SSID")),
            ("fixed", 18, urwid.Text("BSSID")),
            ("fixed", 4, urwid.Text("CH")),
            ("fixed", 6, urwid.Text("RSSI")),
            ("fixed", 22, urwid.Text("GPS")),
        ])
        self._status = urwid.Text(("dim", "  Press [s] to start wardriving"))
        self._body = urwid.WidgetPlaceholder(self._table)

        pile = urwid.Pile([
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(pile)

    # ------------------------------------------------------------------
    # Public API (called by sniffers wrapper)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Called every UI tick (~0.5s)."""
        if self._waiting_gps:
            if self.state.gps_fix_valid:
                self._waiting_gps = False
                self._begin_wardriving()
            else:
                sats = self.state.gps_satellites
                self._status.set_text(
                    ("warning", f"  Waiting for GPS fix... Satellites: {sats}")
                )
            return

        if not self._running:
            n = len(self._seen)
            if n:
                self._status.set_text(
                    ("dim", f"  Stopped. {n} unique networks found. [s]Start  [x]Clear")
                )
            return

        # Auto-start next scan cycle after interval
        if not self._scanning and self._scan_done_time > 0:
            if time.time() - self._scan_done_time >= _SCAN_INTERVAL:
                self._start_scan_cycle()

        # Update status
        n = len(self._seen)
        self._status.set_text(
            ("success",
             f"  Wardriving RUNNING  |  Cycle: {self._cycle_count}  |  "
             f"Unique: {n}  |  [s]Stop  [x]Clear")
        )

    def handle_serial_line(self, line: str) -> None:
        """Parse network lines from ESP32 scan output."""
        if not self._scanning:
            return
        if line.startswith('"'):
            net = self.net_mgr.parse_network_line(line)
            if net:
                self._cycle_nets.append(net)
        if "Scan results printed" in line:
            self._finish_scan_cycle()

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _try_start(self) -> None:
        """Check GPS and start wardriving or show dialog."""
        if not self.state.gps_available:
            dialog = InfoDialog(
                "GPS not available.\nWardriving requires GPS.",
                lambda: self._app.dismiss_overlay(),
                title="No GPS",
            )
            self._app.show_overlay(dialog, 40, 7)
            return

        if not self.state.gps_fix_valid:
            def _on_answer(yes: bool):
                self._app.dismiss_overlay()
                if yes:
                    self._waiting_gps = True
                    self._status.set_text(
                        ("warning", "  Waiting for GPS fix...")
                    )

            dialog = ConfirmDialog(
                "GPS has no fix. Wait for fix?", _on_answer
            )
            self._app.show_overlay(dialog, 40, 7)
            return

        self._begin_wardriving()

    def _begin_wardriving(self) -> None:
        """GPS is ready — start continuous scanning."""
        self._running = True
        self.state.wardriving_running = True
        self._cycle_count = 0
        self._scan_done_time = 0
        self._start_scan_cycle()

    def _stop_wardriving(self) -> None:
        """Stop the wardriving loop."""
        if self._scanning:
            self.serial.send_command(CMD_STOP)
        self._running = False
        self._scanning = False
        self._waiting_gps = False
        self.state.wardriving_running = False
        n = len(self._seen)
        self.state.wardriving_networks = n
        self._status.set_text(
            ("warning", f"  Stopped. {n} unique networks. [s]Start  [x]Clear")
        )

    # ------------------------------------------------------------------
    # Scan cycles
    # ------------------------------------------------------------------

    def _start_scan_cycle(self) -> None:
        """Begin one WiFi scan cycle."""
        self._scanning = True
        self._cycle_nets = []
        self.serial.send_command(CMD_SCAN_NETWORKS)

    def _finish_scan_cycle(self) -> None:
        """Process results from one scan cycle."""
        self._scanning = False
        self._cycle_count += 1
        self._scan_done_time = time.time()

        new_count = 0
        for net in self._cycle_nets:
            if not net.bssid:
                continue
            # Save to loot (with GPS)
            if self._loot:
                saved = self._loot.save_wardriving_network(net)
                if saved:
                    new_count += 1
            # Update in-memory best-RSSI table
            bssid = net.bssid
            try:
                new_rssi = int(net.rssi)
            except (ValueError, TypeError):
                new_rssi = -100
            if bssid in self._seen:
                try:
                    old_rssi = int(self._seen[bssid].rssi)
                except (ValueError, TypeError):
                    old_rssi = -100
                if new_rssi > old_rssi:
                    self._seen[bssid] = net
            else:
                self._seen[bssid] = net

        self.state.wardriving_networks = len(self._seen)
        self._update_table()

    def _update_table(self) -> None:
        """Rebuild the display table from seen networks."""
        rows = []
        # Sort by RSSI (strongest first)
        sorted_nets = sorted(
            self._seen.values(),
            key=lambda n: int(n.rssi) if n.rssi.lstrip("-").isdigit() else -100,
            reverse=True,
        )
        for net in sorted_nets:
            # GPS coords for display
            lat = self.state.gps_latitude
            lon = self.state.gps_longitude
            if is_private():
                gps_str = mask_coords_str(f"{lat},{lon}")
            else:
                gps_str = f"{lat:.5f},{lon:.5f}" if lat or lon else "—"
            rssi_attr = NetworkManager.rssi_level(net.rssi)
            rows.append([
                ("weight", 2, urwid.Text(mask_ssid(net.ssid))),
                ("fixed", 18, urwid.Text(("dim", mask_mac(net.bssid)))),
                ("fixed", 4, urwid.Text(net.channel)),
                ("fixed", 6, urwid.Text((rssi_attr, net.rssi))),
                ("fixed", 22, urwid.Text(("dim", gps_str))),
            ])
        self._table.set_rows(rows)

    def _clear(self) -> None:
        """Clear all results."""
        self._seen.clear()
        self._cycle_count = 0
        self.state.wardriving_networks = 0
        self._table.clear()
        self._status.set_text(("dim", "  Cleared. Press [s] to start wardriving"))

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        if key == "s":
            if self._running or self._waiting_gps:
                self._stop_wardriving()
            else:
                self._try_start()
            return None
        if key == "x":
            if not self._running:
                self._clear()
            return None
        return super().keypress(size, key)
