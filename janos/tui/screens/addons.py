"""Add-ons screen — firmware flash + AIO v2 interface control + LoRa + Cloud."""

import queue
import threading
import time

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...flash_manager import FlashManager
from ...aio_manager import AioManager
from ...lora_manager import LoRaManager
from ...loot_manager import LootManager
from ...upload_manager import wpasec_configured, upload_wpasec_all, download_wpasec_passwords
from ...config import FLASH_BOARDS
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.info_dialog import InfoDialog
from ..widgets.text_input_dialog import TextInputDialog


class _AddonItem(urwid.WidgetWrap):
    def __init__(self, key: str, label: str, active: bool = False) -> None:
        if active:
            text = urwid.Text(("success", f"  [{key}] {label}  [ON]"))
        else:
            text = urwid.Text(("default", f"  [{key}] {label}"))
        super().__init__(text)


class _BoardPickerDialog(urwid.WidgetWrap):
    """Pick board variant for flashing. Calls callback(board_key) or callback(None)."""

    def __init__(self, callback) -> None:
        self._callback = callback
        lines = [
            ("dialog_title", "\n  Select target board:\n\n"),
        ]
        for i, (key, profile) in enumerate(FLASH_BOARDS.items(), 1):
            lines.append(("default", f"  [{i}] {profile['label']}\n"))
        lines.append(("dim", "\n  [Esc] Cancel\n"))
        text = urwid.Text(lines, align="left")
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="Board")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)
        self._keys = list(FLASH_BOARDS.keys())

    def keypress(self, size, key):
        if key == "esc":
            self._callback(None)
            return None
        for i, board_key in enumerate(self._keys, 1):
            if key == str(i):
                self._callback(board_key)
                return None
        return key

    def selectable(self) -> bool:
        return True


class _BaudPickerDialog(urwid.WidgetWrap):
    """Pick baud rate for GPS. Calls callback(baud) or callback(None)."""

    def __init__(self, rates: list, callback) -> None:
        self._callback = callback
        self._rates = rates
        lines = [("dialog_title", "\n  Select baud rate:\n\n")]
        for i, rate in enumerate(rates, 1):
            default = " (default)" if rate == 9600 else ""
            lines.append(("default", f"  [{i}] {rate}{default}\n"))
        lines.append(("dim", "\n  [Esc] Cancel\n"))
        text = urwid.Text(lines, align="left")
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="Baud Rate")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def keypress(self, size, key):
        if key == "esc":
            self._callback(None)
            return None
        for i, rate in enumerate(self._rates, 1):
            if key == str(i):
                self._callback(rate)
                return None
        return key

    def selectable(self) -> bool:
        return True


class AddOnsScreen(urwid.WidgetWrap):
    """Add-ons menu with firmware flashing, AIO control, LoRa, and live log."""

    def __init__(self, state: AppState, serial: SerialManager, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot
        self._upload_result: str | None = None
        self._flash = FlashManager()
        self._lora = LoRaManager()
        self._lora._on_node = self._on_mc_node
        self._lora._on_message = self._on_mc_message
        self._flashing = False
        self._installing_aio = False
        self._gps_pending_device = ""
        self._reconnect_pending = False
        self._reconnect_at: float = 0.0
        self._last_menu_key = ""  # track menu state for rebuild

        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._log = LogViewer(max_lines=500)
        self._status = urwid.Text(("dim", ""))

        log_label = urwid.AttrMap(
            urwid.Text(("dim", "  ── Output ──")), "default",
        )

        self._menu_height = 3  # initial estimate
        self._pile = urwid.Pile([
            ("fixed", self._menu_height, self._listbox),
            ("pack", log_label),
            self._log,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._pile)
        self._rebuild_menu()

    def _menu_key(self) -> str:
        """Key representing current menu state — rebuild only when changed."""
        lora_mode = self._lora.mode if self._lora.running else ""
        return (
            f"{self.state.gps_available},"
            f"{self.state.aio_available},"
            f"{self.state.aio_gps},{self.state.aio_lora},"
            f"{self.state.aio_sdr},{self.state.aio_usb},"
            f"{self._lora.running},{lora_mode},"
            f"{wpasec_configured()}"
        )

    def _rebuild_menu(self) -> None:
        self._walker.clear()
        self._walker.append(_AddonItem("1", "Flash ESP32-C5 Firmware"))

        # External GPS (when no GPS detected at startup)
        if self.state.gps_available:
            gps_dev = self._app.gps.device
            self._walker.append(_AddonItem("g", f"GPS ({gps_dev})", True))
        else:
            self._walker.append(_AddonItem("g", "External GPS"))

        if self.state.aio_available:
            self._walker.append(
                _AddonItem("2", "GPS", self.state.aio_gps))
            self._walker.append(
                _AddonItem("3", "LORA", self.state.aio_lora))
            self._walker.append(
                _AddonItem("4", "SDR", self.state.aio_sdr))
            self._walker.append(
                _AddonItem("5", "USB", self.state.aio_usb))

            # LoRa sub-features (only when LORA is ON)
            if self.state.aio_lora:
                self._walker.append(urwid.Divider("─"))
                self._walker.append(_AddonItem(
                    "6", "LoRa Sniffer",
                    self._lora.running and self._lora.mode == "sniffer",
                ))
                self._walker.append(_AddonItem(
                    "7", "LoRa Scanner",
                    self._lora.running and self._lora.mode == "scanner",
                ))
                self._walker.append(_AddonItem(
                    "8", "Balloon Tracker",
                    self._lora.running and self._lora.mode == "tracker",
                ))
                self._walker.append(urwid.Divider("─"))
                self._walker.append(_AddonItem(
                    "9", "MeshCore Sniffer",
                    self._lora.running and self._lora.mode == "meshcore",
                ))
                self._walker.append(_AddonItem(
                    "0", "Meshtastic Sniffer",
                    self._lora.running and self._lora.mode == "meshtastic",
                ))
        else:
            self._walker.append(_AddonItem("2", "Install AIO v2 Control"))

        # Cloud: WPA-sec (only when token configured)
        if wpasec_configured():
            self._walker.append(urwid.Divider("─"))
            self._walker.append(urwid.Text(("bold", "  ── Cloud ──")))
            self._walker.append(_AddonItem("u", "WPA-sec Upload"))
            self._walker.append(_AddonItem("p", "WPA-sec Passwords"))

        # Update menu height in the Pile
        new_height = len(self._walker) + 1
        if new_height != self._menu_height:
            self._menu_height = new_height
            self._pile.contents[0] = (
                self._listbox, ("given", new_height),
            )

        self._last_menu_key = self._menu_key()
        self._update_status_hint()

    def _update_status_hint(self) -> None:
        if self._flashing or self._installing_aio:
            return
        if self._lora.running:
            mode = self._lora.mode.capitalize()
            self._status.set_text(
                ("attack_active",
                 f"  LoRa {mode} RUNNING "
                 f"({self._lora.packets_received} pkts)  [s]Stop  [x]Clear"))
            return
        if self.state.aio_available:
            if self.state.aio_lora:
                self._status.set_text(
                    ("dim",
                     "  [1]Flash  [g]GPS  [2-5]AIO  [6]Sniff  [7]Scan  "
                     "[8]Track  [9]Mesh  [0]Meshtastic  [x]Clear"))
            else:
                self._status.set_text(
                    ("dim", "  [1]Flash  [g]GPS  [2-5]AIO toggle  [x]Clear"))
        else:
            self._status.set_text(
                ("dim", "  [1]Flash  [g]GPS  [2]Install AIO  [x]Clear"))

    # ------------------------------------------------------------------
    # Refresh (called every 1s by app._tick)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # Drain flash output queue
        while not self._flash.queue.empty():
            try:
                line, attr = self._flash.queue.get_nowait()
                self._log.append(line, attr)
            except queue.Empty:
                break

        # Drain LoRa output queue
        while not self._lora.queue.empty():
            try:
                line, attr = self._lora.queue.get_nowait()
                self._log.append(line, attr)
            except queue.Empty:
                break

        # Poll WPA-sec upload/download result
        if self._upload_result is not None:
            msg = self._upload_result
            self._upload_result = None
            self._app.show_overlay(
                InfoDialog(msg, lambda: self._app.dismiss_overlay(), title="Cloud"),
                50, 8,
            )

        # Update LoRa state in shared AppState (for creature animation etc.)
        self.state.lora_running = self._lora.running
        self.state.lora_mode = self._lora.mode if self._lora.running else ""
        if self._lora.running:
            self.state.lora_packets = self._lora.packets_received
            self._update_status_hint()

        if self._flash.running:
            self._status.set_text(
                ("attack_active", "  FLASHING... Please wait"),
            )
            return

        # Flash just finished
        if self._flash.done and not self._reconnect_pending:
            self._flash.done = False
            self._flashing = False
            self.state.flashing = False
            if self._flash.success:
                self._reconnect_pending = True
                self._reconnect_at = time.time() + 3
                self._status.set_text(
                    ("success", "  Flash OK! Reconnecting serial in 3s..."),
                )
            else:
                self._status.set_text(
                    ("error", "  Flash FAILED — check log above  [x] Clear"),
                )

        # Deferred serial reconnect
        if self._reconnect_pending and time.time() >= self._reconnect_at:
            self._reconnect_pending = False
            self._reconnect_serial()

        # Rebuild menu if AIO state changed (or LoRa mode changed)
        if self._menu_key() != self._last_menu_key:
            self._rebuild_menu()

    # ------------------------------------------------------------------
    # Flash firmware
    # ------------------------------------------------------------------

    def _start_flash(self) -> None:
        if self._flashing:
            self._status.set_text(("warning", "  Flash already in progress"))
            return

        # Step 1: pick board
        dialog = _BoardPickerDialog(self._on_board_picked)
        self._app.show_overlay(dialog, 52, 11)

    def _on_board_picked(self, board: str | None) -> None:
        self._app.dismiss_overlay()
        if board is None:
            return

        # XIAO: release serial BEFORE showing confirm dialog
        # (user must press BOOT+RESET which disconnects the port)
        if board == "xiao":
            self.state.flashing = True  # suppress reconnect polling
            if self.state.connected:
                try:
                    self._app._loop.remove_watch_file(self.serial.fd)
                except Exception:
                    pass
                self.serial.close()
                self.state.connected = False
                self._app._serial_watched = False

        # Step 2: confirm flash
        profile = FLASH_BOARDS[board]
        if board == "xiao":
            hint = ("\nHold BOOT + press RESET, then release BOOT.\n"
                    "Press Yes when device is in bootloader mode.")
        else:
            hint = "\nesptool will auto-reset into bootloader."

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self._begin_flash(board=board)
            elif board == "xiao":
                # Cancelled — clear flashing flag and try to reconnect
                self.state.flashing = False
                self._reconnect_serial()

        dialog = ConfirmDialog(
            f"Flash {profile['label']}?\n"
            f"Serial will disconnect during flash.{hint}",
            on_confirm,
        )
        self._app.show_overlay(dialog, 56, 13)

    def _begin_flash(self, board: str = "wroom",
                     erase: bool = False) -> None:
        self._flashing = True
        self.state.flashing = True
        self._log.clear()
        self._log.append("Starting firmware flash...", "attack_active")

        port = self.serial.device

        # Close serial so esptool can use the port
        if self.state.connected:
            try:
                self._app._loop.remove_watch_file(self.serial.fd)
            except Exception:
                pass
            self.serial.close()
            self.state.connected = False
            self._log.append(f"Serial port {port} released.", "dim")
            self._log.append("", "default")

        self._flash.start(port=port, erase=erase, board=board)

    # ------------------------------------------------------------------
    # Serial reconnect (after flash)
    # ------------------------------------------------------------------

    def _reconnect_serial(self) -> None:
        self._log.append("Reconnecting serial port...", "dim")
        try:
            self.serial.setup()
            self.state.connected = True
            self._app._loop.watch_file(
                self.serial.fd, self._app._on_serial_data,
            )
            self._log.append(
                f"Serial reconnected: {self.serial.device}", "success",
            )
            self._status.set_text(
                ("success", "  Flash complete! Serial OK.  [x] Clear"),
            )
        except Exception as exc:
            self._log.append(f"Reconnect failed: {exc}", "warning")
            self._log.append("Restart JanOS or replug device.", "warning")
            self._status.set_text(
                ("warning", "  Flash done, serial not reconnected.  [x] Clear"),
            )

    # ------------------------------------------------------------------
    # AIO v2 control
    # ------------------------------------------------------------------

    def _toggle_aio(self, feature: str) -> None:
        """Toggle an AIO feature via direct GPIO (pinctrl).

        Instant — no subprocess chains, no threads needed.
        """
        attr = f"aio_{feature}"
        current = getattr(self.state, attr, False)
        new_val = not current

        ok = AioManager.toggle(feature, new_val)
        if ok:
            setattr(self.state, attr, new_val)
            state_str = "ON" if new_val else "OFF"
            self._log.append(
                f"AIO {feature.upper()} → {state_str}", "success",
            )
            # Auto-stop LoRa if LORA toggled OFF
            if feature == "lora" and not new_val and self._lora.running:
                self._lora.stop()
                self._log.append("LoRa stopped (LORA OFF)", "warning")
                self.state.lora_packets = 0
        else:
            self._log.append(
                f"Failed to toggle {feature.upper()}", "error",
            )
        self._rebuild_menu()

    def _install_aio(self) -> None:
        """Install aiov2_ctl from GitHub."""
        if self._installing_aio:
            return
        self._installing_aio = True
        self._log.clear()
        self._status.set_text(
            ("attack_active", "  Installing aiov2_ctl..."))

        def _callback(line: str, attr: str) -> None:
            # Queue for thread safety — drain in refresh()
            self._flash.queue.put((line, attr))
            # Check if install finished
            if "installed successfully" in line.lower():
                self._installing_aio = False
                self.state.aio_available = AioManager.is_installed()
                # Try to get initial status
                if self.state.aio_available:
                    status = AioManager.get_status()
                    if status:
                        self.state.aio_gps = status.get("gps", False)
                        self.state.aio_lora = status.get("lora", False)
                        self.state.aio_sdr = status.get("sdr", False)
                        self.state.aio_usb = status.get("usb", False)
            elif "failed" in line.lower() or "error" in line.lower():
                self._installing_aio = False

        AioManager.install(_callback)

    # ------------------------------------------------------------------
    # LoRa features
    # ------------------------------------------------------------------

    # Map key → (mode, start_method_name)
    _LORA_KEYS = {
        "6": "sniffer", "7": "scanner", "8": "tracker",
        "9": "meshcore", "0": "meshtastic",
    }

    def _start_lora(self, key: str) -> None:
        """Start, stop, or switch a LoRa operation based on key."""
        target_mode = self._LORA_KEYS.get(key, "")
        if self._lora.running:
            same_mode = self._lora.mode == target_mode
            self._lora.stop()
            self.state.lora_packets = 0
            self.state.mc_nodes = 0
            self.state.mc_messages = 0
            if same_mode:
                # Toggle off
                self._rebuild_menu()
                return
            # Switch to different mode — fall through to start

        self._log.clear()
        if key == "6":
            self._lora.start_sniffer()
        elif key == "7":
            self._lora.start_scanner()
        elif key == "8":
            self._lora.start_tracker()
        elif key == "9":
            self._lora.start_meshcore()
        elif key == "0":
            self._lora.start_meshtastic()
        self._rebuild_menu()

    def _on_mc_node(self, node_id, ntype, name, lat, lon, rssi, snr):
        self._app.loot.save_meshcore_node(node_id, ntype, name, lat, lon, rssi, snr)
        # Recount from disk (dedup-safe)
        from pathlib import Path
        csv_path = Path(self._app.loot.session_path) / "meshcore_nodes.csv"
        if csv_path.is_file():
            try:
                lines = sum(1 for _ in open(csv_path, encoding="utf-8"))
                self.state.mc_nodes = max(0, lines - 1)
            except OSError:
                pass

    def _on_mc_message(self, channel, message, rssi):
        self._app.loot.save_meshcore_message(channel, message, rssi)
        self.state.mc_messages += 1

    # ------------------------------------------------------------------
    # External GPS
    # ------------------------------------------------------------------

    _BAUD_RATES = [4800, 9600, 19200, 38400, 57600, 115200]

    def _start_gps_setup(self) -> None:
        """Show dialog to configure external GPS device."""
        if self.state.gps_available:
            # GPS already connected — offer disconnect
            def on_confirm(yes: bool) -> None:
                self._app.dismiss_overlay()
                if yes:
                    self._disconnect_gps()
            dialog = ConfirmDialog(
                f"GPS connected on {self._app.gps.device}.\n"
                "Disconnect GPS?",
                on_confirm,
            )
            self._app.show_overlay(dialog, 50, 8)
            return

        dialog = TextInputDialog(
            "GPS device path",
            self._on_gps_device_entered,
            initial="/dev/ttyUSB0",
        )
        self._app.show_overlay(dialog, 50, 8)

    def _on_gps_device_entered(self, device: str | None) -> None:
        self._app.dismiss_overlay()
        if not device:
            return
        self._gps_pending_device = device
        # Show baud rate picker
        dialog = _BaudPickerDialog(self._BAUD_RATES, self._on_baud_picked)
        self._app.show_overlay(dialog, 40, len(self._BAUD_RATES) + 6)

    def _on_baud_picked(self, baud: int | None) -> None:
        self._app.dismiss_overlay()
        if baud is None:
            return
        device = self._gps_pending_device
        self._connect_gps(device, baud)

    def _connect_gps(self, device: str, baud: int) -> None:
        """Try to open GPS on given device/baud and register with event loop."""
        gps = self._app.gps
        # Close existing if any
        if gps.available:
            self._disconnect_gps()

        gps.device = device
        gps._baud = baud
        if gps._try_open(device):
            self.state.gps_available = True
            try:
                self._app._loop.watch_file(gps.fd, self._app._on_gps_data)
            except Exception:
                pass
            self.state.gps_external = True
            self._log.append(f"GPS connected: {device} @ {baud}", "success")
            self._rebuild_menu()
        else:
            dialog = InfoDialog(
                f"Failed to open GPS on {device}.\n"
                "Check device path and permissions.",
                lambda: self._app.dismiss_overlay(),
            )
            self._app.show_overlay(dialog, 50, 8)

    def _disconnect_gps(self) -> None:
        gps = self._app.gps
        if gps.available:
            try:
                self._app._loop.remove_watch_file(gps.fd)
            except Exception:
                pass
            gps.close()
        self.state.gps_available = False
        self.state.gps_external = False
        self._log.append("GPS disconnected", "warning")
        self._rebuild_menu()

    # ------------------------------------------------------------------
    # WPA-sec cloud
    # ------------------------------------------------------------------

    def _upload_wpasec(self) -> None:
        """Upload .pcap handshakes to WPA-sec."""
        if not self._loot:
            return
        loot_dir = self._loot.loot_root
        self._status.set_text(("warning", "  Uploading handshakes to WPA-sec..."))

        def _do():
            _up, _total, msg = upload_wpasec_all(loot_dir)
            self._upload_result = f"WPA-sec: {msg}"

        threading.Thread(target=_do, daemon=True).start()

    def _download_wpasec(self) -> None:
        """Download cracked passwords from WPA-sec."""
        if not self._loot:
            return
        loot_dir = self._loot.loot_root
        self._status.set_text(("warning", "  Downloading passwords from WPA-sec..."))

        def _do():
            ok, count, msg = download_wpasec_passwords(loot_dir)
            self._upload_result = f"WPA-sec: {msg}"

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        if key == "g":
            self._start_gps_setup()
            return None
        if key == "1" and not self._flashing:
            self._start_flash()
            return None
        if key == "2":
            if self.state.aio_available:
                self._toggle_aio("gps")
            else:
                self._install_aio()
            return None
        if self.state.aio_available and key in ("3", "4", "5"):
            features = {"3": "lora", "4": "sdr", "5": "usb"}
            self._toggle_aio(features[key])
            return None
        # LoRa features (only when LORA ON)
        if self.state.aio_lora and key in ("6", "7", "8", "9", "0"):
            self._start_lora(key)
            return None
        # Stop running LoRa operation
        if key == "s" and self._lora.running:
            self._lora.stop()
            self.state.lora_packets = 0
            return None
        # WPA-sec cloud (only when token configured)
        if key == "u" and wpasec_configured():
            self._upload_wpasec()
            return None
        if key == "p" and wpasec_configured():
            self._download_wpasec()
            return None
        if key == "x":
            if not self._flashing and not self._installing_aio:
                self._log.clear()
                self._update_status_hint()
            return None
        return super().keypress(size, key)
