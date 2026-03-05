"""Header widget — system stats bar."""

import os
import urwid

from ..app_state import AppState
from ..privacy import is_private


def _read_cpu_temp() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return f"{int(f.read().strip()) / 1000:.0f}°C"
    except Exception:
        return ""


def _read_ram() -> str:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    info[parts[0]] = int(parts[1])
        total = info.get("MemTotal:", 0)
        avail = info.get("MemAvailable:", 0)
        used = total - avail
        return f"{used // 1024}/{total // 1024}MB"
    except Exception:
        return ""


def _read_load() -> str:
    try:
        load1, _, _ = os.getloadavg()
        return f"{load1:.1f}"
    except Exception:
        return ""


class HeaderWidget(urwid.WidgetWrap):
    """Top-of-screen header with system stats."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._text = urwid.Text("", align="center")
        widget = urwid.AttrMap(self._text, "header")
        super().__init__(widget)
        self.refresh()

    def refresh(self) -> None:
        parts = []

        # System stats
        temp = _read_cpu_temp()
        ram = _read_ram()
        load = _read_load()
        if temp:
            parts.append(("header", f" CPU:{temp} "))
        if ram:
            parts.append(("header", f" RAM:{ram} "))
        if load:
            parts.append(("header", f" Load:{load} "))

        if not self.state.connected:
            parts.append(("footer_alert", " DISCONNECTED "))
        if is_private():
            parts.append(("header_private", " PRIVATE MODE "))

        if not parts:
            parts.append(("header", " "))

        self._text.set_text(parts)
