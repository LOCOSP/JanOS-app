"""Tab bar widget and tab switching logic."""

import urwid


class TabBar(urwid.WidgetWrap):
    """Horizontal tab bar — switch with 1-5 keys or Tab."""

    def __init__(self, labels: list[str], on_switch=None) -> None:
        self._labels = labels
        self._active = 0
        self._on_switch = on_switch
        self._columns = urwid.Columns([], dividechars=1)
        super().__init__(self._columns)
        self._rebuild()

    @property
    def active(self) -> int:
        return self._active

    @active.setter
    def active(self, index: int) -> None:
        if 0 <= index < len(self._labels) and index != self._active:
            self._active = index
            self._rebuild()
            if self._on_switch:
                self._on_switch(index)

    def _rebuild(self) -> None:
        cols = []
        for i, label in enumerate(self._labels):
            tag = f" {i + 1}:{label} "
            if i == self._active:
                w = urwid.AttrMap(urwid.Text(tag, align="center"), "tab_active")
            else:
                w = urwid.AttrMap(urwid.Text(tag, align="center"), "tab_inactive")
            cols.append(w)
        self._columns.contents = [(c, self._columns.options("weight", 1)) for c in cols]

    def next_tab(self) -> None:
        self.active = (self._active + 1) % len(self._labels)

    def prev_tab(self) -> None:
        self.active = (self._active - 1) % len(self._labels)
