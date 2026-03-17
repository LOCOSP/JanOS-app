"""BT Wardriving screen — continuous BLE scan with GPS geo-tagging."""

import re
import threading
import time
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...loot_manager import LootManager
from ...upload_manager import wigle_configured, upload_wigle, find_wardriving_csvs
from ...privacy import mask_mac, mask_coords_str, is_private
from ...config import CMD_SCAN_BT, CMD_STOP
from ..widgets.data_table import DataTable
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.info_dialog import InfoDialog


# Delay between scan cycles (seconds) — BLE scan takes ~10s, add small gap
_SCAN_INTERVAL = 2.0


class BTWardrivingScreen(urwid.WidgetWrap):
    """Continuous BLE scanning with GPS coordinates per device.

    Press [s] to start/stop.  Devices are de-duplicated by MAC —
    only the strongest RSSI observation is kept.
    Uses the same wardriving.csv (WiGLE format) with Type=BLE.
    """

    def __init__(self, state: AppState, serial: SerialManager,
                 loot: LootManager | None, app) -> None:
        self.state = state
        self.serial = serial
        self._loot = loot
        self._app = app

        # Wardriving state
        self._running = False
        self._waiting_gps = False
        self._scanning = False          # currently in a scan cycle
        self._scan_done_time: float = 0  # when last cycle finished
        self._cycle_count = 0
        self._upload_result: str | None = None
        # mac -> (name, rssi)
        self._seen: dict[str, tuple[str, int]] = {}
        # Current cycle devices
        self._cycle_devs: list[tuple[str, int, str]] = []  # (mac, rssi, name)
        self._scan_started = False  # got "BLE scan starting" line

        # BT device parser regex: "  1. AA:BB:CC:DD:EE:FF  RSSI: -42 dBm  Name: Foo"
        self._bt_device_re = re.compile(
            r'^\s*\d+\.\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)\s*dBm'
            r'(?:\s+Name:\s*(.+?))?(\s*\[AirTag\]|\s*\[SmartTag\])?\s*$'
        )

        # UI
        self._table = DataTable([
            ("weight", 2, urwid.Text("Name")),
            ("fixed", 18, urwid.Text("MAC")),
            ("fixed", 6, urwid.Text("RSSI")),
            ("fixed", 22, urwid.Text("GPS")),
        ])
        hint = "  Press [s] to start BT wardriving"
        if wigle_configured():
            hint += "  [w]WiGLE"
        self._status = urwid.Text(("dim", hint))
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
        # Check for pending upload result
        if self._upload_result is not None:
            msg = self._upload_result
            self._upload_result = None
            self._show_upload_result(msg)

        if self._waiting_gps:
            if self.state.gps_fix_valid:
                self._waiting_gps = False
                self._begin_wardriving()
            else:
                sats = self.state.gps_satellites
                vis = self.state.gps_satellites_visible
                sat_str = f"Sat:{sats}" if sats else f"Vis:{vis}" if vis else "no satellites"
                self._status.set_text(
                    ("warning", f"  Waiting for GPS fix... {sat_str}")
                )
            return

        # Check scan timeout (BLE scan ~10s, auto-finish after 15s)
        self._check_scan_timeout()

        if not self._running:
            n = len(self._seen)
            if n:
                extras = ""
                if wigle_configured():
                    extras += "  [w]WiGLE"
                self._status.set_text(
                    ("dim", f"  Stopped. {n} unique BLE devices. [s]Start  [x]Clear{extras}")
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
             f"  BT Wardriving RUNNING  |  Cycle: {self._cycle_count}  |  "
             f"Unique: {n}  |  [s]Stop  [x]Clear")
        )

    def handle_serial_line(self, line: str) -> None:
        """Parse BT device lines from ESP32 scan output."""
        if not self._scanning:
            return
        stripped = line.strip()

        # Detect scan start
        if "BLE scan starting" in stripped:
            self._scan_started = True
            return

        # Parse device line
        m = self._bt_device_re.match(stripped)
        if m:
            mac, rssi_s, name, _tag = m.groups()
            rssi = int(rssi_s)
            name = (name or "").strip()
            self._cycle_devs.append((mac, rssi, name))
            return

        # Detect scan end: "Summary:" line or "Found X devices:"
        if stripped.startswith("Summary:") or "=== BLE Scan Results ===" in stripped:
            return  # wait for Summary to finish cycle
        if stripped.startswith("Found ") and "devices:" in stripped:
            return
        # The scan is done when we see Summary and then no more device lines
        # Use timeout-based completion (handled in refresh via _scan_done heuristic)

        # Detect explicit end
        if self._scan_started and ("Summary:" in stripped or "scan complete" in stripped.lower()):
            self._finish_scan_cycle()

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _try_start(self) -> None:
        """Check ESP32 + GPS and start BT wardriving or show dialog."""
        if not self.state.connected:
            self._app.wait_for_esp32(self._try_start)
            return
        if not self.state.gps_available:
            dialog = InfoDialog(
                "GPS not available.\nBT Wardriving requires GPS.",
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
        """GPS is ready — start continuous BLE scanning."""
        self._running = True
        self.state.bt_wardriving_running = True
        self._cycle_count = 0
        self._scan_done_time = 0
        self._start_scan_cycle()

    def _stop_wardriving(self) -> None:
        """Stop the BT wardriving loop."""
        if self._scanning:
            self.serial.send_command(CMD_STOP)
        self._running = False
        self._scanning = False
        self._waiting_gps = False
        self.state.bt_wardriving_running = False
        n = len(self._seen)
        self.state.bt_wardriving_devices = n
        extras = ""
        if wigle_configured():
            extras += "  [w]WiGLE"
        self._status.set_text(
            ("warning", f"  Stopped. {n} unique BLE devices. [s]Start  [x]Clear{extras}")
        )

    # ------------------------------------------------------------------
    # Scan cycles
    # ------------------------------------------------------------------

    def _start_scan_cycle(self) -> None:
        """Begin one BLE scan cycle (ESP32 bt_scan = 10s scan)."""
        self._scanning = True
        self._scan_started = False
        self._cycle_devs = []
        self.serial.send_command(CMD_SCAN_BT)
        # Set a timeout — BLE scan takes ~10-12s, auto-finish after 15s
        self._scan_start_time = time.time()

    def _finish_scan_cycle(self) -> None:
        """Process results from one BLE scan cycle."""
        self._scanning = False
        self._scan_started = False
        self._cycle_count += 1
        self._scan_done_time = time.time()

        new_count = 0
        for mac, rssi, name in self._cycle_devs:
            # Save to loot (with GPS) in WiGLE format
            if self._loot:
                saved = self._loot.save_wardriving_bt(mac, rssi, name)
                if saved:
                    new_count += 1
            # Update in-memory best-RSSI table
            if mac in self._seen:
                _old_name, old_rssi = self._seen[mac]
                if rssi > old_rssi:
                    self._seen[mac] = (name, rssi)
            else:
                self._seen[mac] = (name, rssi)

        self.state.bt_wardriving_devices = len(self._seen)
        self._update_table()

    def _check_scan_timeout(self) -> None:
        """Auto-finish scan cycle if it exceeds timeout (15s)."""
        if self._scanning and hasattr(self, '_scan_start_time'):
            if time.time() - self._scan_start_time > 15.0:
                self._finish_scan_cycle()

    def _update_table(self) -> None:
        """Rebuild the display table from seen devices."""
        rows = []
        # Sort by RSSI (strongest first)
        sorted_devs = sorted(
            self._seen.items(),
            key=lambda item: item[1][1],
            reverse=True,
        )
        for mac, (name, rssi) in sorted_devs:
            # GPS coords for display
            lat = self.state.gps_latitude
            lon = self.state.gps_longitude
            if is_private():
                gps_str = mask_coords_str(lat, lon)
            else:
                gps_str = f"{lat:.5f},{lon:.5f}" if (lat is not None and lon is not None and (lat or lon)) else "\u2014"
            if rssi > -50:
                rssi_attr = "success"
            elif rssi > -70:
                rssi_attr = "default"
            else:
                rssi_attr = "dim"
            rows.append([
                ("weight", 2, urwid.Text(name or "\u2014")),
                ("fixed", 18, urwid.Text(("dim", mask_mac(mac)))),
                ("fixed", 6, urwid.Text((rssi_attr, str(rssi)))),
                ("fixed", 22, urwid.Text(("dim", gps_str))),
            ])
        self._table.set_rows(rows)

    def _clear(self) -> None:
        """Clear all results."""
        self._seen.clear()
        self._cycle_count = 0
        self.state.bt_wardriving_devices = 0
        self._table.clear()
        hint = "  Cleared. Press [s] to start BT wardriving"
        if wigle_configured():
            hint += "  [w]WiGLE"
        self._status.set_text(("dim", hint))

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _upload_wigle(self) -> None:
        """Upload wardriving CSV(s) to WiGLE in a background thread."""
        if not self._loot:
            return
        loot_dir = self._loot.loot_root
        csvs = find_wardriving_csvs(loot_dir)
        if not csvs:
            self._app.show_overlay(
                InfoDialog("No wardriving data to upload.",
                           lambda: self._app.dismiss_overlay(), title="WiGLE"),
                45, 7,
            )
            return
        self._status.set_text(("warning", f"  Uploading {len(csvs)} file(s) to WiGLE..."))

        def _do():
            uploaded, errors = 0, []
            for csv_path in csvs:
                ok, msg = upload_wigle(csv_path)
                if ok:
                    uploaded += 1
                else:
                    errors.append(msg)
            result = f"WiGLE: {uploaded}/{len(csvs)} uploaded"
            if errors:
                result += f"\n{errors[0]}"
            self._upload_result = result

        threading.Thread(target=_do, daemon=True).start()

    def _show_upload_result(self, message: str) -> None:
        """Show upload result dialog (called from main thread)."""
        self._app.show_overlay(
            InfoDialog(message, lambda: self._app.dismiss_overlay(), title="Upload"),
            50, 8,
        )

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
        if key == "w" and not self._running:
            if wigle_configured():
                self._upload_wigle()
            else:
                self._app.show_overlay(
                    InfoDialog("WiGLE not configured.\nSet JANOS_WIGLE_NAME and\nJANOS_WIGLE_TOKEN env vars.",
                               lambda: self._app.dismiss_overlay(), title="WiGLE"),
                    45, 9,
                )
            return None
        return super().keypress(size, key)
