"""Startup check dialog — shows dependency and device status on launch."""

import shutil
import subprocess
import sys

import urwid


def _which(tool: str) -> bool:
    """Check if a system tool is available on PATH."""
    return shutil.which(tool) is not None


def _apt_install(package: str) -> bool:
    """Try to install a system package via apt-get. Returns True on success."""
    try:
        subprocess.check_call(
            ["apt-get", "install", "-y", "-qq", package],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


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
        widget = urwid.AttrMap(box, "default")
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
            lines.append(("banner", "\n  Press any key to continue...\n"))
        else:
            lines.append(("banner", f"\n  Starting in {self._countdown}...\n"))
        self._status_text.set_text(lines)

    def add_check(self, status: str, text: str) -> None:
        """Dynamically append a check line and refresh display."""
        self._checks.append((status, text))
        self._rebuild_text()

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

    # USB serial devices — list all detected ports with chip type
    try:
        from ...serial_manager import list_usb_serial_devices
        usb_devs = list_usb_serial_devices()
        for dev_path, desc, is_esp in usb_devs:
            if is_esp and dev_path == device and connected:
                checks.append(("ok", f"ESP32 {dev_path} ({desc})"))
            elif is_esp:
                checks.append(("info", f"ESP32 {dev_path} ({desc}) — not connected"))
            else:
                checks.append(("info", f"USB  {dev_path} ({desc})"))
        if not usb_devs and not device:
            checks.append(("info", "No USB serial devices detected"))
    except Exception:
        # Fallback to simple check
        if connected:
            checks.append(("ok", f"ESP32 {device}"))
        elif device:
            checks.append(("fail", f"ESP32 {device} — not connected"))
        else:
            checks.append(("info", "ESP32 — no device (Advanced attacks only)"))

    # scapy check (needed for Dragon Drain / MITM)
    try:
        import scapy.all  # noqa: F401
        checks.append(("ok", "scapy (Dragon Drain / MITM)"))
    except ImportError:
        checks.append(("info", "scapy not installed — pip install scapy"))

    # bleak check (needed for RACE attack)
    try:
        import bleak  # noqa: F401
        checks.append(("ok", "bleak (RACE BLE)"))
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "bleak"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            checks.append(("ok", "bleak (just installed)"))
        except Exception:
            checks.append(("info", "bleak not installed — pip install bleak"))

    # pybluez check (needed for BlueDucky)
    try:
        import bluetooth  # noqa: F401
        checks.append(("ok", "pybluez (BlueDucky)"))
    except ImportError:
        checks.append(("info", "pybluez not available (system site-packages)"))

    # System tool checks — auto-install if missing
    for tool, apt_pkg, purpose in [
        ("tcpdump", "tcpdump", "MITM pcap capture"),
        ("airmon-ng", "aircrack-ng", "Dragon Drain monitor mode"),
        ("bdaddr", None, "RACE MAC spoofing"),
        ("parecord", "pulseaudio-utils", "RACE audio capture"),
    ]:
        if _which(tool):
            checks.append(("ok", f"{tool} ({purpose})"))
        elif apt_pkg:
            installed = _apt_install(apt_pkg)
            if installed:
                checks.append(("ok", f"{tool} (just installed)"))
            else:
                checks.append(("info", f"{tool} not found — apt install {apt_pkg}"))
        else:
            checks.append(("info", f"{tool} not found ({purpose})"))

    # WiFi interfaces — show all with driver/chipset (like wifite)
    try:
        from ...serial_manager import list_wifi_interfaces
        wifi_ifaces = list_wifi_interfaces()
        has_monitor = False
        for iface, mode, driver, chipset in wifi_ifaces:
            label = iface
            if driver:
                label += f"  {driver}"
            if chipset:
                label += f"  {chipset}"
            if mode == "monitor":
                checks.append(("ok", f"WiFi {label} [monitor]"))
                has_monitor = True
            else:
                checks.append(("info", f"WiFi {label}"))
        if wifi_ifaces and not has_monitor:
            checks.append(("info", "No monitor iface (airmon-ng start wlanX)"))
        elif not wifi_ifaces:
            checks.append(("info", "No WiFi interfaces detected"))
    except Exception:
        checks.append(("info", "WiFi — iw not available"))

    # GPS check (optional — never a failure)
    if gps_available:
        checks.append(("ok", f"GPS {gps_device}"))
    else:
        checks.append(("info", "GPS not found"))

    # AIO v2 check (optional — never a failure)
    try:
        from ...aio_manager import AioManager
        if AioManager.is_installed():
            checks.append(("ok", "AIO v2 (pinctrl)"))
        else:
            checks.append(("info", "AIO v2 not available"))
    except Exception:
        checks.append(("info", "AIO v2 not available"))

    return checks
