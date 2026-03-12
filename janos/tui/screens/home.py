"""Sidebar panel — always-visible left panel with logo + app stats."""

import os
import time
from collections import Counter
from pathlib import Path

import urwid

from ... import __version__
from ...app_state import AppState
from ...loot_manager import LootManager
from ...privacy import mask_coords_str, is_private
from ..widgets.creature import get_creature_state, get_frame

LOGO = (
    "     ██╗ █████╗ ███╗   ██╗ ██████╗ ███████╗\n"
    "     ██║██╔══██╗████╗  ██║██╔═══██╗██╔════╝\n"
    "     ██║███████║██╔██╗ ██║██║   ██║███████╗\n"
    "██   ██║██╔══██║██║╚██╗██║██║   ██║╚════██║\n"
    "╚█████╔╝██║  ██║██║ ╚████║╚██████╔╝███████║\n"
    " ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝        by LOCOSP"
)


class SidebarPanel(urwid.WidgetWrap):
    """Always-visible sidebar with ASCII logo and live app stats."""

    def selectable(self):
        """Sidebar is display-only — never steal focus from main panel."""
        return False

    def __init__(self, state: AppState, loot: LootManager, gps=None) -> None:
        self.state = state
        self.loot = loot
        self._gps = gps

        self._frame_tick = 0

        self._logo = urwid.Text(("banner", LOGO))
        self._version = urwid.Text(("dim", f"  v{__version__}"))
        self._device = urwid.Text("")
        self._fw_version = urwid.Text("")
        self._aio_line = urwid.Text("")
        self._lora_line = urwid.Text("")
        self._runtime = urwid.Text("")
        self._gps_line1 = urwid.Text("")
        self._gps_line2 = urwid.Text("")
        self._networks = urwid.Text("")
        self._net_bands = urwid.Text("")
        self._net_auth = urwid.Text("")
        self._packets = urwid.Text("")
        self._forms = urwid.Text("")
        self._captures = urwid.Text("")
        self._loot_info = urwid.Text("")
        self._mc_line = urwid.Text("")
        self._bt_line = urwid.Text("")
        self._loot_total = urwid.Text("")
        self._loot_total2 = urwid.Text("")
        self._loot_total3 = urwid.Text("")
        self._ops = urwid.Text("")

        sep = urwid.Divider("─")

        items = [
            self._logo,
            self._version,
            self._device,
            self._fw_version,
            sep,
            self._runtime,
            self._gps_line1,
            self._gps_line2,
            self._networks,
            self._net_bands,
            self._net_auth,
            self._packets,
            self._forms,
            self._captures,
            urwid.Divider("─"),
            self._loot_info,
            self._mc_line,
            self._bt_line,
            self._loot_total,
            self._loot_total2,
            self._loot_total3,
            urwid.Divider("─"),
            self._aio_line,
            self._lora_line,
            urwid.Divider("─"),
            self._ops,
        ]
        walker = urwid.SimpleFocusListWalker(items)
        listbox = urwid.ListBox(walker)
        super().__init__(listbox)
        self.refresh()

    # ------------------------------------------------------------------

    def _count_loot_files(self) -> dict:
        """Count loot files in the current session directory."""
        counts: dict = {"pcap": 0, "hccapx": 0, "hc22000": 0, "passwords": 0, "et_captures": 0,
                        "mc_nodes": 0, "mc_messages": 0, "bt_devices": 0, "bt_airtags": 0,
                        "bt_devices_gps": 0}
        if not self.loot.active:
            return counts
        session = Path(self.loot.session_path)
        hs_dir = session / "handshakes"
        if hs_dir.is_dir():
            for f in hs_dir.iterdir():
                if f.suffix == ".pcap":
                    counts["pcap"] += 1
                elif f.suffix == ".hccapx":
                    counts["hccapx"] += 1
                elif f.suffix == ".22000":
                    counts["hc22000"] += 1
        pw_file = session / "portal_passwords.log"
        if pw_file.is_file() and pw_file.stat().st_size > 0:
            try:
                counts["passwords"] = sum(1 for _ in open(pw_file, encoding="utf-8"))
            except OSError:
                pass
        et_file = session / "evil_twin_capture.log"
        if et_file.is_file() and et_file.stat().st_size > 0:
            try:
                counts["et_captures"] = sum(1 for _ in open(et_file, encoding="utf-8"))
            except OSError:
                pass
        mc_nodes_file = session / "meshcore_nodes.csv"
        if mc_nodes_file.is_file():
            try:
                lines = sum(1 for _ in open(mc_nodes_file, encoding="utf-8"))
                counts["mc_nodes"] = max(0, lines - 1)
            except OSError:
                pass
        mc_msgs_file = session / "meshcore_messages.log"
        if mc_msgs_file.is_file():
            try:
                counts["mc_messages"] = sum(1 for _ in open(mc_msgs_file, encoding="utf-8"))
            except OSError:
                pass
        bt_file = session / "bt_devices.csv"
        if bt_file.is_file():
            try:
                gps_count = 0
                total = 0
                for i, line in enumerate(open(bt_file, encoding="utf-8")):
                    if i == 0:
                        continue
                    total += 1
                    parts = line.strip().split(",")
                    if len(parts) >= 8:
                        try:
                            lat = float(parts[-2])
                            lon = float(parts[-1])
                            if lat != 0.0 or lon != 0.0:
                                gps_count += 1
                        except ValueError:
                            pass
                counts["bt_devices"] = total
                counts["bt_devices_gps"] = gps_count
            except OSError:
                pass
        bt_at_file = session / "bt_airtag.log"
        if bt_at_file.is_file():
            try:
                counts["bt_airtags"] = sum(1 for _ in open(bt_at_file, encoding="utf-8"))
            except OSError:
                pass
        return counts

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # AIO v2 interfaces
        if self.state.aio_available:
            parts = []
            for feat, val in [("GPS", self.state.aio_gps),
                              ("LORA", self.state.aio_lora),
                              ("SDR", self.state.aio_sdr),
                              ("USB", self.state.aio_usb)]:
                if val:
                    parts.append(("success", f"{feat}:ON"))
                else:
                    parts.append(("dim", f"{feat}:OFF"))
            markup = [("bold", "  AIO  ")]
            for i, p in enumerate(parts):
                markup.append(p)
                if i < len(parts) - 1:
                    markup.append(("dim", " │ "))
            self._aio_line.set_text(markup)
        else:
            self._aio_line.set_text("")

        # LoRa packets (when LORA is on and packets received)
        if self.state.aio_lora and self.state.lora_packets > 0:
            self._lora_line.set_text(
                ("success", f"  LoRa Packets: {self.state.lora_packets}"))
        else:
            self._lora_line.set_text("")

        # Device
        if self.state.connected:
            self._device.set_text(
                ("success", f"  {self.state.device}  Connected")
            )
        else:
            self._device.set_text(
                ("error", f"  {self.state.device}  DISCONNECTED")
            )

        # Firmware version (detected from ESP32 boot banner)
        if self.state.firmware_version:
            self._fw_version.set_text(
                ("dim", f"  Firmware v{self.state.firmware_version}")
            )

        # Runtime
        if self.state.start_time > 0:
            elapsed = int(time.time() - self.state.start_time)
            mm, ss = divmod(elapsed, 60)
            hh, mm = divmod(mm, 60)
            self._runtime.set_text(
                ("bold", f"  Runtime  {hh:02d}:{mm:02d}:{ss:02d}")
            )

        # GPS
        if self.state.gps_available and self.state.gps_fix_valid:
            q = {0: "NoFix", 1: "GPS", 2: "DGPS"}.get(
                self.state.gps_fix_quality, "Fix"
            )
            if is_private():
                coords = mask_coords_str(
                    self.state.gps_latitude, self.state.gps_longitude
                )
            else:
                coords = (
                    f"{self.state.gps_latitude:.6f}, "
                    f"{self.state.gps_longitude:.6f}"
                )
            self._gps_line1.set_text(
                ("success", f"  GPS  {q} | Sat:{self.state.gps_satellites}")
            )
            self._gps_line2.set_text(("dim", f"    {coords}"))
        elif self.state.gps_available:
            vis = self.state.gps_satellites_visible
            sat_info = f" | Vis:{vis}" if vis else ""
            self._gps_line1.set_text(("warning", f"  GPS  Waiting for fix{sat_info}"))
            self._gps_line2.set_text("")
        else:
            self._gps_line1.set_text("")
            self._gps_line2.set_text("")

        # --- Network stats ---
        nets = self.state.networks
        total = len(nets)
        self._networks.set_text(
            ("default", f"  Networks {total}")
        )

        # Band breakdown
        band_cnt = Counter()
        for n in nets:
            b = n.band.strip() if n.band else "?"
            band_cnt[b] += 1
        band_parts = []
        for b in ("2.4GHz", "5GHz"):
            if band_cnt.get(b, 0):
                band_parts.append(f"{b}:{band_cnt[b]}")
        if not band_parts and total:
            for b, c in band_cnt.most_common():
                band_parts.append(f"{b}:{c}")
        self._net_bands.set_text(
            ("dim", f"    {' │ '.join(band_parts)}") if band_parts else ("dim", "")
        )

        # Auth breakdown
        auth_cnt = Counter()
        for n in nets:
            a = n.auth.strip() if n.auth else "Open"
            auth_cnt[a] += 1
        auth_parts = [f"{a}:{c}" for a, c in auth_cnt.most_common()]
        self._net_auth.set_text(
            ("dim", f"    {' │ '.join(auth_parts)}") if auth_parts else ("dim", "")
        )

        # Other stats
        self._packets.set_text(
            ("default", f"  Packets  {self.state.sniffer_packets}")
        )
        self._forms.set_text(
            ("default", f"  Forms    {self.state.submitted_forms}")
        )
        self._captures.set_text(
            ("default", f"  Captures {len(self.state.evil_twin_captured_data)}")
        )

        # --- Loot ---
        loot = self._count_loot_files()
        loot_parts = []
        if loot["pcap"]:
            loot_parts.append(f"PCAP:{loot['pcap']}")
        if loot["hccapx"]:
            loot_parts.append(f"HCCAPX:{loot['hccapx']}")
        if loot["hc22000"]:
            loot_parts.append(f"22K:{loot['hc22000']}")
        if loot["passwords"]:
            loot_parts.append(f"PWD:{loot['passwords']}")
        if loot["et_captures"]:
            loot_parts.append(f"ET:{loot['et_captures']}")
        if loot_parts:
            self._loot_info.set_text(
                ("success", f"  Loot: {' │ '.join(loot_parts)}")
            )
        else:
            self._loot_info.set_text(("dim", "  Loot: —"))

        # MeshCore loot (current session)
        mc_n = loot.get("mc_nodes", 0)
        mc_m = loot.get("mc_messages", 0)
        if mc_n or mc_m:
            self._mc_line.set_text(
                ("success", f"  MC  Nodes:{mc_n} │ Msgs:{mc_m}")
            )
        else:
            self._mc_line.set_text("")

        # BT loot (current session)
        bt_d = loot.get("bt_devices", 0)
        bt_a = loot.get("bt_airtags", 0)
        bt_g = loot.get("bt_devices_gps", 0)
        if bt_d or bt_a:
            bt_text = f"  BT  Devices:{bt_d} │ AirTags:{bt_a}"
            if bt_g:
                bt_text += f" │ BT+GPS:{bt_g}"
            self._bt_line.set_text(("success", bt_text))
        else:
            self._bt_line.set_text("")

        # --- Total Loot (all sessions) ---
        totals = self.loot.loot_totals
        if totals.get("sessions", 0) > 0:
            # Line 1: WiFi loot
            tp = [f"S:{totals['sessions']}"]
            if totals.get("pcap"):
                tp.append(f"PCAP:{totals['pcap']}")
            if totals.get("hccapx"):
                tp.append(f"HCCAPX:{totals['hccapx']}")
            if totals.get("hc22000"):
                tp.append(f"22K:{totals['hc22000']}")
            if totals.get("passwords"):
                tp.append(f"PWD:{totals['passwords']}")
            if totals.get("et_captures"):
                tp.append(f"ET:{totals['et_captures']}")
            self._loot_total.set_text(
                ("bold", f"  WiFi  {' │ '.join(tp)}")
            )
            # Line 2: BT totals
            tp_bt = []
            bt_total_d = totals.get("bt_devices", 0)
            bt_total_a = totals.get("bt_airtags", 0)
            bt_total_g = totals.get("bt_devices_gps", 0)
            if bt_total_d:
                tp_bt.append(f"Dev:{bt_total_d}")
            if bt_total_a:
                tp_bt.append(f"AT:{bt_total_a}")
            if bt_total_g:
                tp_bt.append(f"GPS:{bt_total_g}")
            if tp_bt:
                self._loot_total2.set_text(
                    ("bold", f"  BT    {' │ '.join(tp_bt)}")
                )
            else:
                self._loot_total2.set_text("")
            # Line 3: LoRa totals
            tp_lr = []
            mc_total_n = totals.get("mc_nodes", 0)
            mc_total_m = totals.get("mc_messages", 0)
            if mc_total_n:
                tp_lr.append(f"Nodes:{mc_total_n}")
            if mc_total_m:
                tp_lr.append(f"Msgs:{mc_total_m}")
            if tp_lr:
                self._loot_total3.set_text(
                    ("bold", f"  LoRa  {' │ '.join(tp_lr)}")
                )
            else:
                self._loot_total3.set_text("")
        else:
            self._loot_total.set_text("")
            self._loot_total2.set_text("")
            self._loot_total3.set_text("")

        # Animated creature
        creature_state = get_creature_state(self.state)
        text, attr = get_frame(creature_state, self._frame_tick)
        self._ops.set_text((attr, text))
        self._frame_tick += 1
