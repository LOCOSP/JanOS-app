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

LOGO = (
    "     ██╗ █████╗ ███╗   ██╗ ██████╗ ███████╗\n"
    "     ██║██╔══██╗████╗  ██║██╔═══██╗██╔════╝\n"
    "     ██║███████║██╔██╗ ██║██║   ██║███████╗\n"
    "██   ██║██╔══██║██║╚██╗██║██║   ██║╚════██║\n"
    "╚█████╔╝██║  ██║██║ ╚████║╚██████╔╝███████║\n"
    " ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝"
)


class SidebarPanel(urwid.WidgetWrap):
    """Always-visible sidebar with ASCII logo and live app stats."""

    def __init__(self, state: AppState, loot: LootManager, gps=None) -> None:
        self.state = state
        self.loot = loot
        self._gps = gps

        self._logo = urwid.Text(("banner", LOGO))
        self._version = urwid.Text(("dim", f"  v{__version__}"))
        self._device = urwid.Text("")
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
        self._ops = urwid.Text("")

        sep = urwid.Divider("─")

        items = [
            self._logo,
            self._version,
            self._device,
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
        counts: dict = {"pcap": 0, "hccapx": 0, "passwords": 0, "et_captures": 0}
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
        return counts

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # Device
        if self.state.connected:
            self._device.set_text(
                ("success", f"  {self.state.device}  Connected")
            )
        else:
            self._device.set_text(
                ("error", f"  {self.state.device}  DISCONNECTED")
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

        # Active operations
        ops = []
        if self.state.sniffer_running:
            ops.append("SNIFF")
        if self.state.attack_running:
            ops.append("DEAUTH")
        if self.state.blackout_running:
            ops.append("BLACKOUT")
        if self.state.sae_overflow_running:
            ops.append("SAE_OVF")
        if self.state.handshake_running:
            ops.append("HS")
        if self.state.portal_running:
            ops.append("PORTAL")
        if self.state.evil_twin_running:
            ops.append("ET")

        if ops:
            self._ops.set_text(("attack_active", f"  {', '.join(ops)}"))
        else:
            self._ops.set_text(("dim", "  Idle"))
