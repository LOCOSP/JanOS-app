"""Sidebar panel — always-visible right panel with logo + app stats."""

import time
import urwid

from ... import __version__
from ...app_state import AppState

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

    def __init__(self, state: AppState) -> None:
        self.state = state

        self._logo = urwid.Text(("banner", LOGO))
        self._version = urwid.Text(("dim", f"  v{__version__}"))
        self._device = urwid.Text("")
        self._runtime = urwid.Text("")
        self._networks = urwid.Text("")
        self._packets = urwid.Text("")
        self._forms = urwid.Text("")
        self._captures = urwid.Text("")
        self._ops = urwid.Text("")

        sep = urwid.Divider("─")

        pile = urwid.Pile([
            ("pack", self._logo),
            ("pack", self._version),
            ("pack", self._device),
            ("pack", sep),
            ("pack", self._runtime),
            ("pack", self._networks),
            ("pack", self._packets),
            ("pack", self._forms),
            ("pack", self._captures),
            ("pack", sep),
            ("pack", self._ops),
        ])
        super().__init__(pile)
        self.refresh()

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

        # App stats
        self._networks.set_text(
            ("default", f"  Networks {len(self.state.networks)}")
        )
        self._packets.set_text(
            ("default", f"  Packets  {self.state.sniffer_packets}")
        )
        self._forms.set_text(
            ("default", f"  Forms    {self.state.submitted_forms}")
        )
        self._captures.set_text(
            ("default", f"  Captures {len(self.state.evil_twin_captured_data)}")
        )

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
