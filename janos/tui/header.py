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
            ("header", " [ "),
            ("header_device", "JanOS"),
            ("header", " ] "),
            ("header", f"v{__version__} "),
        ]
        if self.state.device:
            parts.append(("header_device", f"// {self.state.device} "))
        if not self.state.connected:
            parts.append(("footer_alert", "// DISCONNECTED "))
        self._text.set_text(parts)
