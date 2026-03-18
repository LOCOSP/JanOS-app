"""Sniffers tab — menu wrapper for Wardriving, BT Wardriving, Packet Sniffer, and Watch Dogs Game."""

import os
import subprocess
import threading
import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ...network_manager import NetworkManager
from ...loot_manager import LootManager
from ...upload_manager import (
    wigle_configured, upload_wigle, find_wardriving_csvs,
)
from ..widgets.info_dialog import InfoDialog
from .sniffer import SnifferScreen
from .wardriving import WardrivingScreen
from .bt_wardriving import BTWardrivingScreen


class SniffersScreen(urwid.WidgetWrap):
    """Menu with sub-screen switching (same pattern as AttacksScreen)."""

    def __init__(self, state: AppState, serial: SerialManager,
                 net_mgr: NetworkManager, loot: LootManager | None,
                 app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._loot = loot
        self._sub_screen = None  # active sub-screen or None (menu)
        self._upload_result: str | None = None

        # Sub-screens
        self._wardriving = WardrivingScreen(state, serial, net_mgr, loot, app)
        self._bt_wardriving = BTWardrivingScreen(state, serial, loot, app)
        self._sniffer = SnifferScreen(state, serial, net_mgr, loot, app)

        # Menu view
        menu_widgets = [
            urwid.Text(("bold", "  \u2500\u2500 Sniffers \u2500\u2500")),
            urwid.Divider(),
            urwid.Text(("default", "  [1] Wardriving WiFi")),
            urwid.Text(("default", "  [2] Wardriving BT")),
            urwid.Text(("default", "  [3] Packet Sniffer")),
            urwid.Divider(),
            urwid.Text(("bold", "  \u2500\u2500 Game \u2500\u2500")),
            urwid.Text(("default", "  [g] Watch Dogs Game")),
            urwid.Divider(),
        ]
        # Cloud upload/download hints (only when configured)
        self._cloud_hints = urwid.Pile([])
        menu_widgets.append(self._cloud_hints)

        self._menu_items = urwid.Pile(menu_widgets)
        self._status = urwid.Text(("dim", "  Select mode"))
        self._menu_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            self._menu_items,
            self._status,
        ]))

        self._body = urwid.WidgetPlaceholder(self._menu_view)
        super().__init__(self._body)

    # ------------------------------------------------------------------
    # Sub-screen management
    # ------------------------------------------------------------------

    def _enter_sub_screen(self, screen) -> None:
        self._sub_screen = screen
        self._body.original_widget = screen

    def _exit_sub_screen(self) -> None:
        self._sub_screen = None
        self._body.original_widget = self._menu_view

    # ------------------------------------------------------------------
    # Cloud upload/download
    # ------------------------------------------------------------------

    def _update_cloud_hints(self) -> None:
        """Rebuild cloud service hints based on configured tokens."""
        hints = []
        if wigle_configured():
            hints.append(urwid.Text(("dim", "  [w] WiGLE Upload")))
        if hints:
            hints.insert(0, urwid.Text(("bold", "  \u2500\u2500 Cloud \u2500\u2500")))
        self._cloud_hints.contents = [(w, ("pack", None)) for w in hints]

    def _launch_game(self) -> None:
        """Launch Watch Dogs game as background overlay (JanOS keeps running)."""
        if self.state.game_running:
            self._status.set_text(("warning", "  Game already running"))
            return

        loot_dir = ""
        if self._loot:
            loot_dir = self._loot.loot_root
        # Find project root (where janos/ package lives)
        pkg_dir = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))

        cmd = [
            "python3", "-m", "janos.game.watchdogs",
            self.state.device or "", loot_dir,
        ]
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        # Under sudo, we need X authority from the real user
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user and "XAUTHORITY" not in env:
            env["XAUTHORITY"] = f"/home/{sudo_user}/.Xauthority"
        # Also allow local X connections (fallback)
        try:
            os.system("xhost +local: >/dev/null 2>&1")
        except Exception:
            pass

        self.state.game_running = True
        self._status.set_text(("success", "  Watch Dogs game launched!"))

        def _monitor():
            try:
                # Drop privileges if running as root (sudo)
                kwargs = dict(
                    cwd=pkg_dir, env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if sudo_user and os.getuid() == 0:
                    import pwd
                    pw = pwd.getpwnam(sudo_user)
                    kwargs["preexec_fn"] = lambda: (
                        os.setgid(pw.pw_gid),
                        os.setuid(pw.pw_uid),
                    )
                proc = subprocess.Popen(cmd, **kwargs)
                proc.wait()
            except Exception:
                pass
            finally:
                self.state.game_running = False

        threading.Thread(target=_monitor, daemon=True).start()

    def _upload_wigle(self) -> None:
        """Upload wardriving CSVs to WiGLE."""
        if not self._loot:
            return
        loot_dir = self._loot.loot_root
        csvs = find_wardriving_csvs(loot_dir)
        if not csvs:
            self._app.show_overlay(
                InfoDialog("No wardriving.csv files found.",
                           lambda: self._app.dismiss_overlay(), title="WiGLE"),
                45, 7,
            )
            return
        self._status.set_text(("warning", f"  Uploading {len(csvs)} file(s) to WiGLE..."))

        def _do():
            results = []
            for csv_path in csvs:
                ok, msg = upload_wigle(csv_path)
                results.append(f"{csv_path.parent.name}: {msg}")
            self._upload_result = "WiGLE: " + "; ".join(results)

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "refresh"):
                self._sub_screen.refresh()
            return
        # Poll background upload/download result
        if self._upload_result is not None:
            msg = self._upload_result
            self._upload_result = None
            self._app.show_overlay(
                InfoDialog(msg, lambda: self._app.dismiss_overlay(), title="Cloud"),
                50, 8,
            )
        # Rebuild cloud hints (tokens may change)
        self._update_cloud_hints()
        # Menu view — update status hints
        parts = []
        if self.state.wardriving_running:
            n = self.state.wardriving_networks
            parts.append(f"WiFi WD RUNNING ({n} networks)")
        if self.state.bt_wardriving_running:
            n = self.state.bt_wardriving_devices
            parts.append(f"BT WD RUNNING ({n} devices)")
        if self.state.sniffer_running:
            parts.append(f"Sniffer RUNNING ({self.state.sniffer_packets} pkts)")
        if parts:
            self._status.set_text(("success", "  " + "  |  ".join(parts)))
        elif self._upload_result is None:
            self._status.set_text(("dim", "  Select mode"))

    def handle_serial_line(self, line: str) -> None:
        if self._sub_screen is not None:
            if hasattr(self._sub_screen, "handle_serial_line"):
                self._sub_screen.handle_serial_line(line)
            return
        # No sub-screen active — still buffer sniffer packets
        # (existing behaviour: sniffer always counts packets)

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        # Sub-screen active
        if self._sub_screen is not None:
            if key == "esc":
                self._exit_sub_screen()
                return None
            return self._sub_screen.keypress(size, key)

        # Menu
        if key == "1":
            self._enter_sub_screen(self._wardriving)
            return None
        if key == "2":
            self._enter_sub_screen(self._bt_wardriving)
            return None
        if key == "3":
            self._enter_sub_screen(self._sniffer)
            return None

        # Game
        if key == "g":
            self._launch_game()
            return None

        # Cloud services (menu only)
        if key == "w":
            if wigle_configured():
                self._upload_wigle()
            else:
                self._app.show_overlay(
                    InfoDialog("WiGLE not configured.\nSet JANOS_WIGLE_NAME and\nJANOS_WIGLE_TOKEN env vars.",
                               lambda: self._app.dismiss_overlay(), title="WiGLE"),
                    45, 9,
                )
            return None
        return super().keypress(size, key)
