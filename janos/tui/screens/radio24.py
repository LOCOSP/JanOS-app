"""2.4G / NFC tab — nRF24, NFC (PN532/ST25R3916) and Zigbee (802.15.4) recon
for the Monster RF (KOPENG firmware).

These radios are Monster-specific (a plain ESP32-C5 doesn't have them). Same
menu + live-log pattern as the SubGHz screen; on boards without the hardware the
firmware answers "Unrecognized command" / init errors, which show in the log.
"""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ..widgets.log_viewer import LogViewer
from ..widgets.text_input_dialog import TextInputDialog


class Radio24Screen(urwid.WidgetWrap):
    """nRF24 / NFC / Zigbee command menu with a live serial log."""

    # (key, label, command, param, prompt, streaming)
    #   param: "" none | "req" required | "opt" optional
    SECTIONS = [
        ("nRF24  2.4GHz", [
            ("1", "Init nRF24",        "init_nrf24",     "",    "", False),
            ("2", "Jammer 2.4G",       "start_jammer24", "opt", "mode: ble|bt|wifi|drone|all", True),
        ]),
        ("NFC  (PN532 / ST25R3916)", [
            ("3", "Show NFC bus",      "get_nfc_bus",    "",    "", False),
            ("4", "Set NFC bus",       "set_nfc_bus",    "req", "i2c | spi", False),
            ("5", "Init NFC",          "init_nfc",       "",    "", False),
            ("6", "Read card",         "nfc_read",       "opt", "timeout_ms (optional)", False),
            ("7", "List saved cards",  "nfc_list",       "",    "", False),
            ("8", "Save last card",    "nfc_save",       "req", "name", False),
            ("9", "Load card",         "nfc_load",       "req", "index | name", False),
            ("0", "Emulate card",      "start_nfc_emulate", "opt", "index | name (optional)", True),
            ("e", "Delete card",       "nfc_delete",     "req", "index | name", False),
            ("d", "Dict status",       "nfc_dict_status", "",   "", False),
            ("p", "Probe PN532 i2c",   "probe_pn532_i2c", "",   "", False),
        ]),
        ("Zigbee  IEEE 802.15.4", [
            ("z", "Start recon",       "start_zig_recon", "opt", "all | 11,15,20  [dwell_ms]", True),
            ("s", "Recon status",      "zig_recon_status", "",  "", False),
            ("l", "List PANs",         "zig_recon_list",  "opt", "all (optional)", False),
            ("n", "List nodes",        "zig_recon_nodes", "req", "pan_id | all", False),
            ("c", "Clear recon",       "zig_recon_clear", "",   "", False),
        ]),
    ]

    def __init__(self, state: AppState, serial: SerialManager, app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._running = ""
        self._actions = {}  # key -> tuple

        menu_rows = [
            urwid.Text(("bold", "  ── 2.4G / NFC / Zigbee  (Monster RF) ──")),
        ]
        for title, items in self.SECTIONS:
            menu_rows.append(urwid.Divider())
            menu_rows.append(urwid.Text(("bold", f"  {title}")))
            for key, label, cmd, param, prompt, streaming in items:
                self._actions[key] = (label, cmd, param, prompt, streaming)
                menu_rows.append(urwid.Text(("default", f"  [{key}] {label}")))
        menu_rows.append(urwid.Divider())
        menu_rows.append(urwid.Text(("dim", "  [x] STOP   [Enter] raw command")))
        self._menu = urwid.Pile(menu_rows)

        self._status = urwid.Text(("dim", "  Ready"))
        self._log = LogViewer(max_lines=400)

        self._view = urwid.Pile([
            ("pack", self._menu),
            ("pack", urwid.Divider()),
            ("pack", self._status),
            ("pack", urwid.Text(("bold", "  ── Output ──"))),
            ("weight", 1, self._log),
        ])
        super().__init__(self._view)

    # ------------------------------------------------------------------
    def _send(self, cmd: str) -> None:
        self._log.append(f">> {cmd}", "warning")
        self.serial.send_command(cmd)

    def _prompt(self, base: str, prompt: str, allow_empty: bool,
                streaming: bool, label: str) -> None:
        def on_val(val):
            self._app.dismiss_overlay()
            if val is None:
                return
            val = val.strip()
            if val:
                cmd = f"{base} {val}"
            elif allow_empty:
                cmd = base
            else:
                return
            self._send(cmd)
            if streaming:
                self._running = label
        self._app.show_overlay(TextInputDialog(prompt, on_val), 60, 8)

    def _raw(self) -> None:
        def on_val(val):
            self._app.dismiss_overlay()
            if val and val.strip():
                self._send(val.strip())
        self._app.show_overlay(TextInputDialog("Command", on_val), 60, 8)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.state.connected:
            self._status.set_text(("error", "  Not connected"))
        elif self.state.board_name and self.state.board_name.upper() != "KOPENG":
            self._status.set_text(
                ("dim", f"  {self.state.board_name}: 2.4G/NFC may be unsupported")
            )
        elif self._running:
            self._status.set_text(("success", f"  RUNNING: {self._running}   ([x] to stop)"))
        else:
            self._status.set_text(("dim", "  Ready"))

    def handle_serial_line(self, line: str) -> None:
        self._log.append(line)
        if "stopped" in line.lower():
            self._running = ""

    def keypress(self, size, key):
        if key == "enter":
            self._raw()
            return None
        if key == "x":
            self._send("stop")
            self._running = ""
            return None
        act = self._actions.get(key)
        if act:
            label, cmd, param, prompt, streaming = act
            if param in ("req", "opt"):
                self._prompt(cmd, prompt, allow_empty=(param == "opt"),
                             streaming=streaming, label=label)
            else:
                self._send(cmd)
                if streaming:
                    self._running = label
            return None
        return super().keypress(size, key)
