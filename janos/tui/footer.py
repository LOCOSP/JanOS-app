"""Status bar footer widget."""

import time
import urwid

from ..app_state import AppState


class StatusBar(urwid.WidgetWrap):
    """Bottom status bar: packets, runtime, alerts, hotkey hints."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._text = urwid.Text("")
        widget = urwid.AttrMap(self._text, "footer")
        super().__init__(widget)
        self.refresh()

    def refresh(self) -> None:
        parts: list = []

        # Packet count
        parts.append(("footer", f" Pkts:{self.state.sniffer_packets}"))
        parts.append(("footer", " | "))

        # Runtime
        if self.state.start_time > 0:
            elapsed = int(time.time() - self.state.start_time)
            mm, ss = divmod(elapsed, 60)
            hh, mm = divmod(mm, 60)
            if hh:
                parts.append(("footer", f"Run:{hh:02d}:{mm:02d}:{ss:02d}"))
            else:
                parts.append(("footer", f"Run:{mm:02d}:{ss:02d}"))
        else:
            parts.append(("footer", "Run:--:--"))
        parts.append(("footer", " | "))

        # Attack alerts
        alerts = []
        if self.state.attack_running:
            alerts.append("DEAUTH")
        if self.state.blackout_running:
            alerts.append("BLACKOUT")
        if self.state.sae_overflow_running:
            alerts.append("SAE_OVF")
        if self.state.handshake_running:
            alerts.append("HANDSHAKE")
        if self.state.sniffer_running:
            alerts.append("SNIFF")
        if self.state.portal_running:
            alerts.append("PORTAL")
        if self.state.evil_twin_running:
            alerts.append("EVIL_TWIN")
        if self.state.firmware_crashed:
            alerts.append("CRASH!")

        if alerts:
            parts.append(("footer_alert", ",".join(alerts)))
        else:
            parts.append(("footer", "Idle"))

        parts.append(("footer", " | "))

        # Hotkey hints
        parts.append(("footer_key", "Tab"))
        parts.append(("footer", ":Switch "))
        parts.append(("footer_key", "q"))
        parts.append(("footer", ":Quit "))

        self._text.set_text(parts)
