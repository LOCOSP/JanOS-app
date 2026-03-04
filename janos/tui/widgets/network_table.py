"""Scrollable network table with RSSI coloring and keyboard selection."""

import urwid

from ...app_state import Network
from ...network_manager import NetworkManager


COLUMNS = [
    ("fixed", 4,  "##"),
    ("weight", 3, "SSID"),
    ("fixed", 18, "BSSID"),
    ("fixed", 4,  "CH"),
    ("fixed", 6,  "RSSI"),
    ("weight", 1, "Auth"),
    ("fixed", 5,  "Band"),
]


class NetworkRow(urwid.WidgetWrap):
    """Single row representing a network."""

    def __init__(self, net: Network, selected: bool = False) -> None:
        self.network = net
        self.selected = selected

        rssi_attr = "rssi_" + NetworkManager.rssi_level(net.rssi)

        cols = urwid.Columns([
            ("fixed", 4,  urwid.Text(net.index[:3])),
            ("weight", 3, urwid.Text(self._trunc(net.ssid, 26))),
            ("fixed", 18, urwid.Text(("dim", net.bssid))),
            ("fixed", 4,  urwid.Text(net.channel[:3])),
            ("fixed", 6,  urwid.Text((rssi_attr, net.rssi[:5]))),
            ("weight", 1, urwid.Text(self._trunc(net.auth, 12))),
            ("fixed", 5,  urwid.Text(net.band[:5])),
        ], dividechars=1)

        mark = "*" if selected else " "
        row = urwid.Columns([
            ("fixed", 2, urwid.Text(("success" if selected else "dim", mark))),
            cols,
        ])

        attr = "table_row_sel" if selected else "table_row"
        widget = urwid.AttrMap(row, attr, focus_map="table_row_sel")
        super().__init__(widget)

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s if len(s) <= n else s[: n - 3] + "..."

    def selectable(self) -> bool:
        return True

    def keypress(self, size, key):
        return key


class NetworkTable(urwid.WidgetWrap):
    """Scrollable table of networks with keyboard navigation and selection."""

    def __init__(self) -> None:
        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._selected: set[str] = set()  # indices of selected networks

        # Header row
        hdr_cols = urwid.Columns([
            ("fixed", 2,  urwid.Text("")),
            ("fixed", 4,  urwid.Text("##")),
            ("weight", 3, urwid.Text("SSID")),
            ("fixed", 18, urwid.Text("BSSID")),
            ("fixed", 4,  urwid.Text("CH")),
            ("fixed", 6,  urwid.Text("RSSI")),
            ("weight", 1, urwid.Text("Auth")),
            ("fixed", 5,  urwid.Text("Band")),
        ], dividechars=1)
        hdr = urwid.AttrMap(hdr_cols, "table_header")

        pile = urwid.Pile([
            ("pack", hdr),
            self._listbox,
        ])
        super().__init__(pile)

    def update(self, networks: list[Network]) -> None:
        """Rebuild the table rows from network list, preserving focus."""
        # Save current focus
        _, old_focus = self._listbox.get_focus()

        self._walker.clear()
        for net in networks:
            sel = net.index in self._selected
            self._walker.append(NetworkRow(net, selected=sel))

        # Restore focus
        if old_focus is not None and self._walker:
            self._listbox.set_focus(min(old_focus, len(self._walker) - 1))

    def toggle_selection(self) -> None:
        """Toggle selection of the focused row."""
        if not self._walker:
            return
        focus_w, idx = self._listbox.get_focus()
        if focus_w is None or idx is None:
            return
        net = focus_w.network
        if net.index in self._selected:
            self._selected.discard(net.index)
        else:
            self._selected.add(net.index)
        # Rebuild that row
        self._walker[idx] = NetworkRow(net, net.index in self._selected)
        self._listbox.set_focus(idx)

    def get_selected_indices(self) -> list[str]:
        return sorted(self._selected)

    def clear_selection(self) -> None:
        self._selected.clear()

    def keypress(self, size, key):
        if key == " " or key == "enter":
            self.toggle_selection()
            return None
        return super().keypress(size, key)
