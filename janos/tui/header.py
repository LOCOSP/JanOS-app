"""Header widget — system stats bar."""

import glob
import os
import urwid

from ..app_state import AppState
from ..privacy import is_private

# Discover battery sysfs path once at import time
_BAT_PATH = ""
for _p in glob.glob("/sys/class/power_supply/*/type"):
    try:
        with open(_p) as _f:
            if _f.read().strip() == "Battery":
                _BAT_PATH = os.path.dirname(_p)
                break
    except OSError:
        pass


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


def _read_battery() -> tuple:
    """Read battery percent and voltage. Returns (percent_str, voltage_str)."""
    if not _BAT_PATH:
        return "", ""
    pct = ""
    volts = ""
    try:
        with open(os.path.join(_BAT_PATH, "capacity")) as f:
            pct = f.read().strip() + "%"
    except Exception:
        pass
    try:
        with open(os.path.join(_BAT_PATH, "voltage_now")) as f:
            uv = int(f.read().strip())
            volts = f"{uv / 1_000_000:.2f}V"
    except Exception:
        pass
    return pct, volts


class HeaderWidget(urwid.WidgetWrap):
    """Top-of-screen header with system stats."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._left = urwid.Text("")
        self._right = urwid.Text("", align="right")
        columns = urwid.Columns([
            self._left,
            ("pack", self._right),
        ])
        widget = urwid.AttrMap(columns, "header")
        super().__init__(widget)
        self.refresh()

    def refresh(self) -> None:
        left = []

        # System stats
        temp = _read_cpu_temp()
        ram = _read_ram()
        load = _read_load()
        if temp:
            left.append(("header", f" CPU:{temp} "))
        if ram:
            left.append(("header", f" RAM:{ram} "))
        if load:
            left.append(("header", f" Load:{load} "))

        if not self.state.connected:
            left.append(("footer_alert", " DISCONNECTED "))
        if is_private():
            left.append(("header_private", " PRIVATE MODE "))

        if not left:
            left.append(("header", " "))

        self._left.set_text(left)

        # Battery (right-aligned)
        pct, volts = _read_battery()
        if pct:
            bat_text = f"BAT:{pct}"
            if volts:
                bat_text += f" {volts}"
            self._right.set_text(("header", f" {bat_text} "))
        else:
            self._right.set_text("")
