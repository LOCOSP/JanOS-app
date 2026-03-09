"""Startup check dialog — shows dependency and device status on launch."""

import subprocess
import sys

import urwid


class StartupScreen(urwid.WidgetWrap):
    """Overlay dialog that displays startup check results.

    Auto-dismisses after countdown if no failures, otherwise waits for keypress.
    """

    def __init__(self, checks: list, has_errors: bool, on_dismiss) -> None:
        self._on_dismiss = on_dismiss
        self._has_errors = has_errors
        self._countdown = 5
        self._checks = checks

        self._status_text = urwid.Text("", align="left")
        self._rebuild_text()
        fill = urwid.Filler(self._status_text, valign="middle")
        box = urwid.LineBox(fill, title="Startup Check")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def _rebuild_text(self) -> None:
        lines = []
        for status, text in self._checks:
            if status == "ok":
                lines.append(("success", f"  [OK]   {text}\n"))
            elif status == "info":
                lines.append(("dim", f"  [--]   {text}\n"))
            else:
                lines.append(("error", f"  [FAIL] {text}\n"))
        if self._has_errors:
            lines.append(("dialog_title", "\n  Press any key to continue...\n"))
        else:
            lines.append(("dialog_title", f"\n  Starting in {self._countdown}...\n"))
        self._status_text.set_text(lines)

    def tick(self, loop, _data=None):
        """Called every second to update countdown."""
        if self._has_errors:
            return
        self._countdown -= 1
        if self._countdown <= 0:
            self._on_dismiss()
            return
        self._rebuild_text()
        loop.set_alarm_in(1, self.tick)

    def keypress(self, size, key):
        self._on_dismiss()
        return None

    def selectable(self) -> bool:
        return True


def run_startup_checks(device: str, connected: bool, gps_available: bool,
                       gps_device: str = "/dev/ttyAMA0") -> list:
    """Run all startup checks. Returns list of (status, description) tuples."""
    checks = []

    # Package checks
    for pkg, import_name in [("urwid", "urwid"), ("pyserial", "serial")]:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", "?")
            checks.append(("ok", f"{pkg} {ver}"))
        except ImportError:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", pkg],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                checks.append(("ok", f"{pkg} (just installed)"))
            except Exception:
                checks.append(("fail", f"{pkg} — pip install {pkg}"))

    # ESP32 check
    if connected:
        checks.append(("ok", f"ESP32 {device}"))
    else:
        checks.append(("fail", f"ESP32 {device} — not connected"))

    # GPS check (optional — never a failure)
    if gps_available:
        checks.append(("ok", f"GPS {gps_device}"))
    else:
        checks.append(("info", "GPS not found"))

    return checks
