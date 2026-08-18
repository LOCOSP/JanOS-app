"""SubGHz tab — CC1101 sub-GHz control for the Monster RF (KOPENG firmware).

The Monster RF (board_name=KOPENG) exposes a ``subghz_*`` command family over
the same serial link used for WiFi. This screen is a thin menu + live log over
those commands. On boards that lack the CC1101 the firmware simply answers
"Unrecognized command", which shows up in the log.
"""

import urwid

from ...app_state import AppState
from ...serial_manager import SerialManager
from ..widgets.log_viewer import LogViewer
from ..widgets.text_input_dialog import TextInputDialog


class SubGHzScreen(urwid.WidgetWrap):
    """Menu of CC1101 sub-GHz commands with a live serial output log."""

    # (key, label, command, needs_param, param_prompt, streaming)
    ACTIONS = [
        ("1", "Status",              "subghz_status",        False, "", False),
        ("2", "RX listen + save",    "subghz_rx",            False, "", True),
        ("3", "Freq analyzer",       "subghz_freq_analyzer", False, "", True),
        ("4", "Scanner",             "subghz_scanner",       False, "", True),
        ("5", "Weather (433.92)",    "subghz_weather",       False, "", True),
        ("6", "TPMS (433.92)",       "subghz_tpms",          False, "", True),
        ("7", "Jammer (cur. freq)",  "subghz_jam",           False, "", True),
        ("f", "Set frequency",       "subghz_freq",          True,  "Frequency MHz (300-1000)", False),
        ("l", "List signals (mem)",  "subghz_list mem",      False, "", False),
        ("L", "List signals (SD)",   "subghz_list sd",       False, "", False),
        ("t", "TX / replay",         "subghz_tx",            True,  "TX: <index> <mem|sd> | tesla", True),
        ("s", "Save mem -> SD",      "subghz_save",          True,  "Save: <mem_idx> | all", False),
        ("k", "Known keys",          "subghz_keys",          False, "", False),
        ("z", "Self-test",           "subghz_selftest",      False, "", False),
        ("c", "Clear captures",      "subghz_clear",         False, "", False),
        ("x", "STOP radio",          "subghz_stop",          False, "", False),
    ]

    def __init__(self, state: AppState, serial: SerialManager, app) -> None:
        self.state = state
        self.serial = serial
        self._app = app
        self._running = ""  # label of the active streaming op (or "")

        menu_rows = [
            urwid.Text(("bold", "  ── SubGHz  (CC1101 / Monster RF) ──")),
            urwid.Divider(),
        ]
        # two-column-ish menu via single texts (keeps it simple/robust)
        for key, label, *_ in self.ACTIONS:
            menu_rows.append(urwid.Text(("default", f"  [{key}] {label}")))
        menu_rows.append(urwid.Divider())
        menu_rows.append(urwid.Text(("dim", "  [Enter] raw subghz_* command")))
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
    # Sending
    # ------------------------------------------------------------------
    def _send(self, cmd: str) -> None:
        self._log.append(f">> {cmd}", "warning")
        self.serial.send_command(cmd)

    def _prompt(self, base: str, prompt: str, streaming: bool, label: str) -> None:
        def on_val(val):
            self._app.dismiss_overlay()
            if val and val.strip():
                self._send(f"{base} {val.strip()}")
                if streaming:
                    self._running = label
        self._app.show_overlay(TextInputDialog(prompt, on_val), 60, 8)

    def _raw(self) -> None:
        def on_val(val):
            self._app.dismiss_overlay()
            if val and val.strip():
                self._send(val.strip())
        self._app.show_overlay(TextInputDialog("SubGHz command", on_val, "subghz_"), 60, 8)

    # ------------------------------------------------------------------
    # App interface
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.state.connected:
            self._status.set_text(("error", "  Not connected"))
        elif self.state.board_name and self.state.board_name.upper() != "KOPENG":
            self._status.set_text(
                ("dim", f"  {self.state.board_name}: sub-GHz may be unsupported")
            )
        elif self._running:
            self._status.set_text(("success", f"  RUNNING: {self._running}   ([x] to stop)"))
        else:
            self._status.set_text(("dim", "  Ready (CC1101)"))

    def handle_serial_line(self, line: str) -> None:
        # Reflect any freq/idle info; just stream everything to the log.
        self._log.append(line)
        if "stopped" in line.lower() or "idle" in line.lower():
            self._running = ""

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------
    def keypress(self, size, key):
        if key == "enter":
            self._raw()
            return None
        for k, label, cmd, needs, prompt, streaming in self.ACTIONS:
            if key == k:
                if needs:
                    self._prompt(cmd, prompt, streaming, label)
                else:
                    self._send(cmd)
                    if streaming:
                        self._running = label
                    elif cmd in ("subghz_stop", "subghz_status"):
                        self._running = ""
                return None
        # let unhandled keys (up/down/pgup) scroll the log
        return super().keypress(size, key)
