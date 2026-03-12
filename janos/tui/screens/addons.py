"""Add-ons screen — firmware flash + AIO v2 interface control + LoRa."""

import queue
import time

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...flash_manager import FlashManager
from ...aio_manager import AioManager
from ...lora_manager import LoRaManager
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog


class _AddonItem(urwid.WidgetWrap):
    def __init__(self, key: str, label: str, active: bool = False) -> None:
        if active:
            text = urwid.Text(("success", f"  [{key}] {label}  [ON]"))
        else:
            text = urwid.Text(("default", f"  [{key}] {label}"))
        super().__init__(text)


class AddOnsScreen(urwid.WidgetWrap):
    """Add-ons menu with firmware flashing, AIO control, LoRa, and live log."""

    def __init__(self, state: AppState, serial: SerialManager, app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._flash = FlashManager()
        self._lora = LoRaManager()
        self._lora._on_node = self._on_mc_node
        self._lora._on_message = self._on_mc_message
        self._flashing = False
        self._installing_aio = False
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
            f"{self.state.aio_available},"
            f"{self.state.aio_gps},{self.state.aio_lora},"
            f"{self.state.aio_sdr},{self.state.aio_usb},"
            f"{self._lora.running},{lora_mode}"
        )

    def _rebuild_menu(self) -> None:
        self._walker.clear()
        self._walker.append(_AddonItem("1", "Flash ESP32-C5 Firmware"))

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
                     "  [1]Flash  [2-5]AIO  [6]Sniff  [7]Scan  "
                     "[8]Track  [9]Mesh  [0]Meshtastic  [x]Clear"))
            else:
                self._status.set_text(
                    ("dim", "  [1]Flash  [2-5]AIO toggle  [x]Clear"))
        else:
            self._status.set_text(
                ("dim", "  [1]Flash  [2]Install AIO  [x]Clear"))

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

        # Update LoRa packet count in shared state
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

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if yes:
                self._begin_flash()

        dialog = ConfirmDialog(
            "Flash ESP32-C5 with latest firmware?\n"
            "Serial will disconnect during flash.\n"
            "esptool will auto-reset into bootloader.",
            on_confirm,
        )
        self._app.show_overlay(dialog, 50, 10)

    def _begin_flash(self, erase: bool = False) -> None:
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

        self._flash.start(port=port, erase=erase)

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
    # Keyboard
    # ------------------------------------------------------------------

    def keypress(self, size, key):
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
        if key == "x":
            if not self._flashing and not self._installing_aio:
                self._log.clear()
                self._update_status_hint()
            return None
        return super().keypress(size, key)
