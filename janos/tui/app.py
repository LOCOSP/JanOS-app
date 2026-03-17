"""Main TUI application — urwid.MainLoop, serial watcher, tab routing."""

import logging
import os
import re
import threading
import time

import urwid

from ..app_state import AppState
from ..serial_manager import SerialManager
from ..network_manager import NetworkManager
from ..loot_manager import LootManager
from ..gps_manager import GpsManager
from ..aio_manager import AioManager
from .. import privacy
from ..config import CRASH_KEYWORDS
from .palette import PALETTE
from .header import HeaderWidget
from .footer import StatusBar
from .tabs import TabBar
from .screens.home import SidebarPanel
from .screens.scan import ScanScreen
from .screens.sniffers import SniffersScreen
from .screens.attacks import AttacksScreen
from .screens.portal import PortalScreen
from .screens.evil_twin import EvilTwinScreen
from .screens.addons import AddOnsScreen
from .screens.map_screen import MapScreen
from .screens.dragon_drain import DragonDrainScreen
from .screens.mitm import MITMScreen
from .widgets.confirm_dialog import ConfirmDialog
from .widgets.info_dialog import InfoDialog
from .widgets.startup_screen import StartupScreen, run_startup_checks

log = logging.getLogger(__name__)

TAB_LABELS = ["Scan", "Sniffers", "Attacks", "Add-ons", "Map"]


class _CrashDialog(urwid.WidgetWrap):
    """Selectable crash overlay — any key dismisses it."""

    def __init__(self, details: str, on_dismiss) -> None:
        self._on_dismiss = on_dismiss
        text = urwid.Text(
            ("crash",
             f"\n  FIRMWARE CRASH DETECTED\n\n"
             f"  {details}\n"
             f"  ESP32 is rebooting.\n"
             f"  Press any key to dismiss.\n"),
            align="left",
        )
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="CRASH")
        widget = urwid.AttrMap(box, "crash")
        super().__init__(widget)

    def keypress(self, size, key):
        self._on_dismiss()
        return None

    def selectable(self) -> bool:
        return True


class JanOSTUI:
    """Top-level TUI controller."""

    def __init__(self, device: str) -> None:
        # State & managers
        self.state = AppState(device=device, start_time=time.time())
        self.serial = SerialManager(device)
        self.net_mgr = NetworkManager(self.state)

        # GPS module — optional, graceful degradation
        self.gps = GpsManager()
        self.state.gps_available = self.gps.setup()

        # AIO v2 module — optional
        self.state.aio_available = AioManager.is_installed()
        if self.state.aio_available:
            status = AioManager.get_status()
            if status:
                self.state.aio_gps = status.get("gps", False)
                self.state.aio_lora = status.get("lora", False)
                self.state.aio_sdr = status.get("sdr", False)
                self.state.aio_usb = status.get("usb", False)

        # Loot manager — save captured data to disk
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.loot = LootManager(app_dir, gps_manager=self.gps)

        # Reset USB hub at startup (fixes XIAO re-enumeration on uConsole)
        self._reset_usb_hub_if_needed()

        # Connect serial — auto-detect port if not specified
        if not device:
            from ..serial_manager import list_usb_serial_devices
            usb_devices = list_usb_serial_devices()
            esp_devices = [d for d in usb_devices if d[2]]
            if esp_devices:
                detected, desc, _ = esp_devices[0]
                log.info("Auto-detected ESP32 on %s (%s)", detected, desc)
                self.serial.device = detected
                self.state.device = detected
                self.state.device_description = desc
        try:
            self.serial.setup()
            self.state.connected = True
            # Probe firmware readiness (XIAO native USB CDC boots slowly)
            if self.serial.probe():
                self.state.esp32_ready = True
                log.info("ESP32 firmware ready")
            else:
                log.info("ESP32 port open but firmware not responding yet (booting)")
        except Exception as exc:
            log.info("Serial: %s", exc)
            self.state.connected = False

        # Detect WiFi interfaces (driver, chipset, mode)
        try:
            from ..serial_manager import list_wifi_interfaces
            self.state.wifi_interfaces = list_wifi_interfaces()
            for iface, mode, drv, chip in self.state.wifi_interfaces:
                log.info("WiFi: %s mode=%s driver=%s chipset=%s", iface, mode, drv, chip)
        except Exception:
            pass

        # Load saved firmware version (from last flash) so sidebar shows it
        # immediately even if boot banner was already sent before we connected
        try:
            from ..updater import get_local_fw_version
            saved_fw = get_local_fw_version()
            if saved_fw:
                self.state.firmware_version = saved_fw.lstrip("v")
                log.info("Loaded saved firmware version: %s", saved_fw)
        except Exception:
            pass

        # Portal & Evil Twin are sub-screens of Attacks — create first
        self._portal = PortalScreen(self.state, self.serial, self, self.loot)
        self._evil_twin = EvilTwinScreen(self.state, self.serial, self.net_mgr, self, self.loot)

        # Advanced attack sub-screens (Python-native, no ESP32)
        self._dragon_drain = DragonDrainScreen(self.state, self, self.loot)
        self._mitm = MITMScreen(self.state, self, self.loot)

        # Main screens
        self._scan = ScanScreen(self.state, self.serial, self.net_mgr, self.loot, self)
        self._sniffer = SniffersScreen(self.state, self.serial, self.net_mgr, self.loot, self)
        self._attacks = AttacksScreen(
            self.state, self.serial, self, self.loot,
            portal=self._portal, evil_twin=self._evil_twin,
            dragon_drain=self._dragon_drain, mitm=self._mitm,
        )

        # Add-ons screen
        self._addons = AddOnsScreen(self.state, self.serial, self)

        # Map screen
        self._map = MapScreen(self.state, self.loot)
        self._map_twinkle_active = False
        self._map_twinkle_alarm = None

        self._screens: list = [
            self._scan,
            self._sniffer,
            self._attacks,
            self._addons,
            self._map,
        ]

        # Sidebar — always-visible left panel with logo + stats
        self._sidebar = SidebarPanel(self.state, self.loot, gps=self.gps)
        self._mobile_mode = False

        # Widgets
        self._header = HeaderWidget(self.state)
        self._tab_bar = TabBar(TAB_LABELS, on_switch=self._on_tab_switch)
        self._footer = StatusBar(
            self.state,
            loot_path=self.loot.session_path if self.loot.active else "",
        )
        self._body = urwid.WidgetPlaceholder(self._screens[0])

        # Left column: tab bar + active screen
        self._left_panel = urwid.Frame(
            body=self._body,
            header=self._tab_bar,
        )

        # Main area: sidebar + right panel
        self._columns = urwid.Columns([
            ("weight", 35, self._sidebar),
            ("weight", 65, self._left_panel),
        ], dividechars=1, focus_column=1)

        # Layout: header + columns + footer
        self._content = urwid.WidgetPlaceholder(self._columns)
        frame = urwid.Frame(
            body=self._content,
            header=self._header,
            footer=self._footer,
        )
        self._frame = frame
        self._overlay_active = False

        # urwid main loop — wrap in Overlay-capable widget
        self._main_widget = urwid.WidgetPlaceholder(frame)
        self._loop = urwid.MainLoop(
            self._main_widget,
            palette=PALETTE,
            unhandled_input=self._unhandled_input,
        )

        # Serial FD watcher (if connected)
        self._serial_watched = False
        self._reconnect_alarm = None
        if self.state.connected:
            self._loop.watch_file(self.serial.fd, self._on_serial_data)
            self._serial_watched = True

        # GPS FD watcher (if available)
        if self.state.gps_available:
            self._loop.watch_file(self.gps.fd, self._on_gps_data)

        # 1-second refresh timer + AIO counter
        self._aio_tick = 0
        self._loop.set_alarm_in(1, self._tick)

        # Background update check (non-blocking, result used after startup screen)
        self._update_version: str | None = None
        self._fw_remote_version: str | None = None
        self._fw_local_version: str | None = None
        self._update_thread = threading.Thread(
            target=self._check_update, daemon=True,
        )
        self._update_thread.start()

        # Startup check dialog
        from ..config import GPS_DEVICE
        checks = run_startup_checks(
            device, self.state.connected, self.state.gps_available, GPS_DEVICE,
        )
        has_errors = any(c[0] == "fail" for c in checks)
        self._startup_screen = StartupScreen(checks, has_errors, on_dismiss=self._dismiss_startup)
        height = len(checks) + 6
        self.show_overlay(self._startup_screen, width=50, height=height)
        if not has_errors:
            self._loop.set_alarm_in(1, self._startup_screen.tick)

    def _dismiss_startup(self) -> None:
        self._startup_screen = None
        self.dismiss_overlay()
        # Show update dialog if a newer version was found
        if self._update_version:
            self._show_update_dialog()
        elif self._fw_remote_version:
            self._show_fw_update_dialog()

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------

    def _check_update(self) -> None:
        """Background thread: check GitHub for newer app + firmware versions."""
        # --- App version ---
        try:
            from ..updater import check_remote_version, is_newer
            from .. import __version__

            remote = check_remote_version(timeout=5)
            if remote and is_newer(remote, __version__):
                self._update_version = remote
                log.info("Update available: %s -> %s", __version__, remote)
        except Exception as exc:
            log.debug("Update check error: %s", exc)

        # --- Firmware version ---
        try:
            from ..updater import (
                check_remote_firmware_version,
                get_local_fw_version,
                is_newer,
            )

            remote_fw = check_remote_firmware_version(timeout=10)
            if remote_fw:
                # Best local version: live ESP32 > saved file > None
                local_fw = (
                    self.state.firmware_version
                    or get_local_fw_version()
                )
                remote_clean = remote_fw.lstrip("v")
                local_clean = (local_fw or "").lstrip("v")
                if not local_fw or is_newer(remote_clean, local_clean):
                    self._fw_remote_version = remote_fw
                    self._fw_local_version = local_fw
                    log.info(
                        "Firmware update available: %s -> %s",
                        local_fw or "unknown",
                        remote_fw,
                    )
        except Exception as exc:
            log.debug("Firmware check error: %s", exc)

    def _show_update_dialog(self) -> None:
        """Show a y/n dialog offering to update."""
        from .. import __version__

        msg = f"Update v{__version__} \u2192 v{self._update_version}?"

        def _on_answer(yes: bool) -> None:
            self.dismiss_overlay()
            if yes:
                self._do_update()
            elif self._fw_remote_version:
                self._show_fw_update_dialog()

        dialog = ConfirmDialog(msg, _on_answer)
        self.show_overlay(dialog, 40, 7)

    def _show_fw_update_dialog(self) -> None:
        """Show an info dialog about available firmware update."""
        # Prefer live ESP32 version, fallback to saved file, then "unknown"
        local = (
            self.state.firmware_version
            or self._fw_local_version
            or "unknown"
        )
        remote = self._fw_remote_version
        msg = (
            f"New firmware {remote} available!\n"
            f"  Current: {local}\n\n"
            f"  Go to Add-ons (tab 4) to flash."
        )
        dialog = InfoDialog(
            msg,
            lambda: self.dismiss_overlay(),
            title="Firmware Update",
        )
        self.show_overlay(dialog, 50, 10)

    def _do_update(self) -> None:
        """Run git pull in background and show result."""
        from ..updater import do_git_pull
        from queue import Queue

        q: Queue = Queue()
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def _callback(line: str, attr: str = "default") -> None:
            q.put((line, attr))

        def _run() -> None:
            do_git_pull(app_dir, _callback)

        threading.Thread(target=_run, daemon=True).start()

        # Show info overlay, poll queue for result
        def _poll(_loop=None, _data=None) -> None:
            result_line = ""
            while not q.empty():
                line, _attr = q.get_nowait()
                result_line = line
            if "complete" in result_line.lower() or "failed" in result_line.lower() or "error" in result_line.lower():
                self.dismiss_overlay()
                dialog = InfoDialog(
                    result_line.strip(),
                    lambda: self.dismiss_overlay(),
                    title="Update",
                )
                self.show_overlay(dialog, 50, 7)
            else:
                self._loop.set_alarm_in(0.5, _poll)

        dialog = InfoDialog(
            "Updating...",
            lambda: None,  # not dismissable yet
            title="Update",
        )
        self.show_overlay(dialog, 40, 7)
        self._loop.set_alarm_in(0.5, _poll)

    # ------------------------------------------------------------------
    # Overlay support (for dialogs)
    # ------------------------------------------------------------------

    def show_overlay(self, widget: urwid.Widget, width: int, height: int) -> None:
        overlay = urwid.Overlay(
            widget,
            self._frame,
            align="center",
            valign="middle",
            width=width,
            height=height,
        )
        self._main_widget.original_widget = overlay
        self._overlay_active = True

    def dismiss_overlay(self) -> None:
        self._main_widget.original_widget = self._frame
        self._overlay_active = False

    # ------------------------------------------------------------------
    # Mobile mode toggle
    # ------------------------------------------------------------------

    def _toggle_mobile(self) -> None:
        self._mobile_mode = not self._mobile_mode
        if self._mobile_mode:
            # Mobile: full-width screen, no sidebar
            self._content.original_widget = self._left_panel
        else:
            # Desktop: sidebar on the right
            self._content.original_widget = self._columns
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    _MAP_TAB = 4  # 0-based index of Map tab
    _MAP_TWINKLE_INTERVAL = 0.3  # seconds between twinkle frames

    def _on_tab_switch(self, index: int) -> None:
        self._body.original_widget = self._screens[index]
        # Auto-hide sidebar on Map tab for full-width map
        if index == self._MAP_TAB:
            self._content.original_widget = self._left_panel
            self._start_map_twinkle()
        else:
            self._stop_map_twinkle()
            if not self._mobile_mode:
                self._content.original_widget = self._columns

    # ------------------------------------------------------------------
    # Map twinkle animation (fast timer only when Map tab is active)
    # ------------------------------------------------------------------

    def _start_map_twinkle(self) -> None:
        """Start fast refresh timer for map point blinking."""
        if not getattr(self, "_map_twinkle_active", False):
            self._map_twinkle_active = True
            self._map_twinkle_alarm = self._loop.set_alarm_in(
                self._MAP_TWINKLE_INTERVAL, self._map_twinkle_tick,
            )

    def _stop_map_twinkle(self) -> None:
        """Stop the fast map refresh timer."""
        self._map_twinkle_active = False
        alarm = getattr(self, "_map_twinkle_alarm", None)
        if alarm:
            self._loop.remove_alarm(alarm)
            self._map_twinkle_alarm = None

    def _map_twinkle_tick(self, loop=None, data=None) -> None:
        """Fast tick for map point twinkle animation."""
        if not self._map_twinkle_active:
            return
        if self._tab_bar.active == self._MAP_TAB:
            self._map._map.twinkle()
        self._map_twinkle_alarm = self._loop.set_alarm_in(
            self._MAP_TWINKLE_INTERVAL, self._map_twinkle_tick,
        )

    # ------------------------------------------------------------------
    # GPS data callback (fired by urwid event loop)
    # ------------------------------------------------------------------

    def _on_gps_data(self) -> None:
        try:
            sentences = self.gps.read_available()
        except Exception:
            return
        if sentences:
            self.gps.process_sentences(sentences)
            fix = self.gps.fix
            self.state.gps_fix_valid = fix.valid
            self.state.gps_latitude = fix.latitude
            self.state.gps_longitude = fix.longitude
            self.state.gps_altitude = fix.altitude
            self.state.gps_satellites = fix.satellites
            self.state.gps_satellites_visible = fix.satellites_visible
            self.state.gps_fix_quality = fix.fix_quality
            self.state.gps_hdop = fix.hdop

    # ------------------------------------------------------------------
    # Serial data callback (fired by urwid event loop)
    # ------------------------------------------------------------------

    def _on_serial_data(self) -> None:
        try:
            lines = self.serial.read_available()
        except Exception as exc:
            log.error("Serial read error (device disconnected?): %s", exc)
            # Save fd BEFORE closing — fileno() fails on closed connection
            saved_fd = None
            try:
                saved_fd = self.serial.fd
            except Exception:
                pass
            self.state.connected = False
            self.state.esp32_ready = False
            self._boot_tick = 0
            self._boot_total = 0
            self._serial_watched = False
            # Remove the FD watcher — device is gone
            if saved_fd is not None:
                try:
                    self._loop.remove_watch_file(saved_fd)
                except Exception:
                    pass
            self.serial.close()
            self._refresh_ui()
            # Don't start reconnect polling during flash — serial is
            # intentionally released and esptool will take over the port.
            if not self.state.flashing:
                self._start_reconnect_polling()
            return

        # Any serial data means firmware is alive
        if lines and not self.state.esp32_ready:
            self.state.esp32_ready = True
            log.info("ESP32 firmware ready (first serial data)")

        crash_lines = []
        for line in lines:
            log.debug("RX: %s", line)
            # Log every serial line to loot
            self.loot.log_serial(line)
            # Firmware version detection from serial output
            # Pattern 1: boot banner — === APP_MAIN START (v1.5.5) ===
            # Pattern 2: ESP-IDF log — I (xxx) main: JanOS version: 1.5.5
            if not self.state.firmware_version:
                if "APP_MAIN START" in line:
                    m = re.search(r"\(v?(\d+\.\d+\.\d+)\)", line)
                    if m:
                        self.state.firmware_version = m.group(1)
                        log.info("Firmware version detected (boot): %s", m.group(1))
                elif "JanOS version:" in line:
                    m = re.search(r"JanOS version:\s*v?(\d+\.\d+\.\d+)", line)
                    if m:
                        self.state.firmware_version = m.group(1)
                        log.info("Firmware version detected (log): %s", m.group(1))
            # Crash detection — collect all crash lines, show ONE overlay
            if self.serial.is_crash_line(line):
                self.state.firmware_crashed = True
                self.state.crash_message = line
                log.warning("Firmware crash detected: %s", line)
                crash_lines.append(line)
                continue
            self._dispatch_line(line)
        if crash_lines:
            self._show_crash_overlay(crash_lines)
        if lines:
            self._refresh_ui()

    def _show_crash_overlay(self, crash_lines: list) -> None:
        """Show a red crash alert overlay (single, selectable)."""
        # Don't stack overlays — dismiss any existing one first
        if self._overlay_active:
            self.dismiss_overlay()
        summary = "\n  ".join(ln[:60] for ln in crash_lines[-4:])
        widget = _CrashDialog(summary, lambda: self.dismiss_overlay())
        self.show_overlay(widget, 65, 12)
        # Reset all running states
        self.state.stop_all()
        self._refresh_ui()

    def _dispatch_line(self, line: str) -> None:
        """Route an incoming serial line to the active screen's handler."""
        active_idx = self._tab_bar.active
        screen = self._screens[active_idx]

        # Always update sniffer count if sniffer is running
        if self.state.sniffer_running:
            self.state.sniffer_buffer.append(line)
            count = self.net_mgr.extract_packet_count(line)
            if count is not None:
                self.state.sniffer_packets = count
            elif re.search(r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}", line):
                self.state.sniffer_packets += 1

        # Route to the active screen
        if hasattr(screen, "handle_serial_line"):
            screen.handle_serial_line(line)

        # Also route to attacks screen when attacks are running (even from other tabs)
        if self.state.any_attack_running() and screen is not self._attacks:
            self._attacks.handle_serial_line(line)

        # Also route to sniffers screen when wardriving is running (even from other tabs)
        if (self.state.wardriving_running or self.state.bt_wardriving_running) and screen is not self._sniffer:
            self._sniffer.handle_serial_line(line)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def _tick(self, loop=None, data=None) -> None:
        self._refresh_ui()
        # Poll GPS every tick as fallback (watch_file may not fire for USB GPS)
        if self.state.gps_available:
            self._on_gps_data()
        # AIO + WiFi status refresh every 10 seconds (non-blocking thread)
        self._aio_tick += 1
        if self._aio_tick >= 10:
            self._aio_tick = 0
            if self.state.aio_available:
                threading.Thread(target=self._refresh_aio, daemon=True).start()
            threading.Thread(target=self._refresh_wifi, daemon=True).start()
        # Background reconnect: if disconnected and no active polling dialog,
        # try to reconnect every 5 seconds (fallback for dismissed dialog)
        # Skip during flash — serial is intentionally released for esptool.
        if not self.state.connected and self.state.device \
                and not self.state.flashing:
            self._reconnect_tick = getattr(self, "_reconnect_tick", 0) + 1
            if self._reconnect_tick >= 5:
                self._reconnect_tick = 0
                if self._try_reconnect():
                    self._refresh_ui()
        # Boot readiness probe: port open but firmware not responding yet
        # Informational only — does NOT block operations
        if self.state.connected and not self.state.esp32_ready \
                and not self.state.flashing:
            self._boot_tick = getattr(self, "_boot_tick", 0) + 1
            self._boot_total = getattr(self, "_boot_total", 0) + 1
            if self._boot_total >= 30:  # 30s timeout — assume ready
                self.state.esp32_ready = True
                log.info("ESP32 boot probe timeout — assuming ready")
                self._refresh_ui()
            elif self._boot_tick >= 2:  # probe every 2s
                self._boot_tick = 0
                if self.serial.probe():
                    self.state.esp32_ready = True
                    log.info("ESP32 firmware ready (after boot wait)")
                    self._refresh_ui()
        self._loop.set_alarm_in(1, self._tick)

    def _refresh_aio(self) -> None:
        """Fetch AIO status in background thread to avoid blocking UI."""
        status = AioManager.get_status()
        if status:
            self.state.aio_gps = status.get("gps", False)
            self.state.aio_lora = status.get("lora", False)
            self.state.aio_sdr = status.get("sdr", False)
            self.state.aio_usb = status.get("usb", False)

    def _refresh_wifi(self) -> None:
        """Refresh WiFi interface list in background thread."""
        try:
            from ..serial_manager import list_wifi_interfaces
            self.state.wifi_interfaces = list_wifi_interfaces()
        except Exception:
            pass

    def _register_serial_watcher(self) -> None:
        """Register serial FD watcher after reconnection."""
        if self._serial_watched or not self.state.connected:
            return
        try:
            self._loop.watch_file(self.serial.fd, self._on_serial_data)
            self._serial_watched = True
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ESP32 wait / reconnect
    # ------------------------------------------------------------------

    def wait_for_esp32(self, on_connected) -> None:
        """Show a polling dialog until ESP32 is detected.

        Use from any screen that needs a serial connection before proceeding.
        If already connected, *on_connected* is called immediately.
        Only checks connection state — esp32_ready is informational (sidebar).
        """
        if self.state.connected:
            on_connected()
            return

        _alarm = [None]
        _waiting = [True]

        def check(loop=None, _data=None):
            if not _waiting[0]:
                return
            if not self.state.connected:
                if self._try_reconnect():
                    pass
                else:
                    _alarm[0] = self._loop.set_alarm_in(2, check)
                    return
            # Connected — proceed immediately
            _waiting[0] = False
            self.dismiss_overlay()
            on_connected()

        msg = (
            "Connect ESP32 via USB.\n\n"
            "Plug in ESP32-C5 to any USB port.\n"
            "Auto-detecting ttyUSB0-3 / ttyACM0-3\n\n"
            "Waiting for ESP32..."
        )
        title = "ESP32 Required"

        def on_dismiss():
            _waiting[0] = False
            if _alarm[0]:
                try:
                    self._loop.remove_alarm(_alarm[0])
                except Exception:
                    pass
            self.dismiss_overlay()

        from .widgets.info_dialog import InfoDialog
        dialog = InfoDialog(msg, on_dismiss, title=title)
        self.show_overlay(dialog, 48, 11)
        _alarm[0] = self._loop.set_alarm_in(2, check)

    # ------------------------------------------------------------------
    # Device reconnection polling
    # ------------------------------------------------------------------

    def _try_reconnect(self) -> bool:
        """Attempt to detect and reconnect ESP32. Returns True on success."""
        from ..serial_manager import detect_esp32_port, list_usb_serial_devices
        detected = detect_esp32_port()
        if not detected:
            return False
        self.serial.device = detected
        self.state.device = detected
        usb_devs = list_usb_serial_devices()
        for d_path, d_desc, d_esp in usb_devs:
            if d_path == detected:
                self.state.device_description = d_desc
                break
        try:
            self.serial.setup()
            self.state.connected = True
            self.state.esp32_ready = self.serial.probe()
            self._register_serial_watcher()
            log.info("Reconnected to %s (ready=%s)", detected, self.state.esp32_ready)
            return True
        except Exception:
            return False

    def _start_reconnect_polling(self) -> None:
        """Show reconnect dialog and poll for USB device every 2s."""
        self._reconnect_alarm = None

        def check_device(loop=None, _data=None):
            if self.state.connected:
                return
            if self._try_reconnect():
                self.dismiss_overlay()
                self._refresh_ui()
                return
            self._reconnect_alarm = self._loop.set_alarm_in(2, check_device)

        msg = (
            "Device disconnected!\n\n"
            "Scanning for ESP32 on\n"
            "ttyUSB0-3 / ttyACM0-3...\n\n"
            "Plug in ESP32 to reconnect\n"
            "or press any key to dismiss."
        )

        dialog = InfoDialog(msg, lambda: self.dismiss_overlay(), title="Device Lost")
        self.show_overlay(dialog, width=40, height=12)
        # Start polling immediately
        self._reconnect_alarm = self._loop.set_alarm_in(2, check_device)

    def _refresh_ui(self) -> None:
        self._header.refresh()
        self._footer.refresh()
        self._sidebar.refresh()
        current = self._screens[self._tab_bar.active]
        if hasattr(current, "refresh"):
            current.refresh()

    # ------------------------------------------------------------------
    # Keyboard input
    # ------------------------------------------------------------------

    def _unhandled_input(self, key: str) -> bool:
        # If overlay is active, only let Esc dismiss it
        if self._overlay_active:
            if key == "esc":
                self.dismiss_overlay()
                return True
            return False

        if key in ("q", "Q"):
            self._confirm_quit()
            return True
        # Private mode toggle
        if key == "P":
            privacy.set_private_mode(not privacy.is_private())
            # Force full UI rebuild (tables need redrawing with masked data)
            self._scan._last_net_count = -1
            self._attacks._last_flags = ""
            self._refresh_ui()
            return True
        # Mobile mode toggle
        if key == "M":
            self._toggle_mobile()
            return True
        if key in ("tab", "right"):
            self._tab_bar.next_tab()
            return True
        if key in ("shift tab", "left"):
            self._tab_bar.prev_tab()
            return True
        # Number keys switch tabs (1-4)
        if key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if idx < len(self._screens):
                self._tab_bar.active = idx
            return True
        # Stop all
        if key == "9":
            if self.state.connected:
                self.serial.send_command("stop")
            # Stop Python-native attacks
            if hasattr(self, '_dragon_drain'):
                self._dragon_drain._stop()
            if hasattr(self, '_mitm'):
                self._mitm._stop()
            self.state.stop_all()
            self._refresh_ui()
            return True
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _confirm_quit(self) -> None:
        def _on_answer(yes: bool) -> None:
            self.dismiss_overlay()
            if yes:
                self._quit()
        dialog = ConfirmDialog("Quit JanOS?", _on_answer)
        self.show_overlay(dialog, 35, 7)

    def _quit(self) -> None:
        # Always send stop — even if flags are out of sync with ESP32 state
        try:
            self.serial.send_command("stop")
            time.sleep(0.3)
            # Second stop for good measure (some modes need it)
            self.serial.send_command("stop")
            time.sleep(0.1)
        except Exception:
            pass
        # Restore WiFi adapters from monitor mode
        self._restore_wifi_monitor()
        self.loot.close()
        self.gps.close()
        self.serial.close()
        raise urwid.ExitMainLoop()

    def _restore_wifi_monitor(self) -> None:
        """Run airmon-ng stop on any monitor mode interfaces."""
        import subprocess
        try:
            from ..serial_manager import list_wifi_interfaces
            for iface, mode, _drv, _chip in list_wifi_interfaces():
                if mode == "monitor":
                    try:
                        subprocess.run(
                            ["sudo", "airmon-ng", "stop", iface],
                            capture_output=True, timeout=10,
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    @staticmethod
    def _reset_usb_hub_if_needed() -> None:
        """Reset USB hub to fix device re-enumeration (e.g. XIAO on uConsole).

        Some USB hubs (QinHeng CH9102) fail to re-enumerate devices after
        a flash cycle or reset.  Toggling the hub's sysfs 'authorized'
        attribute forces re-enumeration without a full system reboot.
        Only runs on Linux when the sysfs path exists.
        """
        hub_auth = "/sys/bus/usb/devices/1-1/authorized"
        if not os.path.exists(hub_auth):
            return
        try:
            with open(hub_auth, "w") as f:
                f.write("0")
            time.sleep(2)
            with open(hub_auth, "w") as f:
                f.write("1")
            time.sleep(3)
            log.info("USB hub reset complete")
        except Exception as exc:
            log.debug("USB hub reset skipped: %s", exc)

    def run(self) -> None:
        self._loop.run()
