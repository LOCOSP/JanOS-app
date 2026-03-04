"""Generic scrollable data table for sniffer results, probes, etc."""

import urwid


class DataRow(urwid.WidgetWrap):
    """Single row of text data."""

    def __init__(self, columns: list[tuple], attr: str = "table_row") -> None:
        cols = urwid.Columns(columns, dividechars=1)
        widget = urwid.AttrMap(cols, attr, focus_map="table_row_sel")
        super().__init__(widget)

    def selectable(self) -> bool:
        return True

    def keypress(self, size, key):
        return key


class DataTable(urwid.WidgetWrap):
    """Scrollable table with a fixed header and arbitrary rows."""

    def __init__(self, header_columns: list[tuple]) -> None:
        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)

        hdr = urwid.AttrMap(
            urwid.Columns(header_columns, dividechars=1),
            "table_header",
        )

        pile = urwid.Pile([
            ("pack", hdr),
            self._listbox,
        ])
        super().__init__(pile)

    def set_rows(self, rows: list[list[tuple]]) -> None:
        """Replace all rows, preserving focus position."""
        _, old_focus = self._listbox.get_focus()
        self._walker.clear()
        for row_cols in rows:
            self._walker.append(DataRow(row_cols))
        if old_focus is not None and self._walker:
            self._listbox.set_focus(min(old_focus, len(self._walker) - 1))

    def clear(self) -> None:
        self._walker.clear()

    @property
    def row_count(self) -> int:
        return len(self._walker)
