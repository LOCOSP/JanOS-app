"""Header widget — compact banner + device info."""

import urwid

from .. import __version__
from ..app_state import AppState


class HeaderWidget(urwid.WidgetWrap):
    """Top-of-screen header showing app name, version, and device."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._text = urwid.Text("", align="center")
        widget = urwid.AttrMap(self._text, "header")
        super().__init__(widget)
        self.refresh()

    def refresh(self) -> None:
        parts = [
            ("header", " JanOS "),
            ("header_device", f"v{__version__}"),
            ("header", "  "),
        ]
        if self.state.device:
            parts.append(("header_device", f"Device: {self.state.device}"))
        if not self.state.connected:
            parts.append(("footer_alert", " [DISCONNECTED]"))
        self._text.set_text(parts)
