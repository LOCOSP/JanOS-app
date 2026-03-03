"""Modal yes/no confirmation dialog overlay."""

import urwid


class ConfirmDialog(urwid.WidgetWrap):
    """A modal overlay asking y/n. Calls *callback(True)* or *callback(False)*."""

    def __init__(self, message: str, callback) -> None:
        self._callback = callback
        text = urwid.Text(("dialog_title", f"\n  {message}\n\n  [y] Yes   [n] No\n"), align="left")
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="Confirm")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def keypress(self, size, key):
        if key in ("y", "Y"):
            self._callback(True)
            return None
        if key in ("n", "N", "esc"):
            self._callback(False)
            return None
        return key

    def selectable(self) -> bool:
        return True
