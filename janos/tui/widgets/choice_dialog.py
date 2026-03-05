"""Modal three-way choice dialog overlay (yes / no / cancel)."""

import urwid


class ChoiceDialog(urwid.WidgetWrap):
    """A modal overlay with three choices.

    Calls *callback('y')*, *callback('n')*, or *callback('c')*.
    """

    def __init__(self, message: str, callback) -> None:
        self._callback = callback
        text = urwid.Text(
            ("dialog_title",
             f"\n  {message}\n\n"
             f"  [y] Yes   [n] No   [c] Cancel\n"),
            align="left",
        )
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title="Choice")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def keypress(self, size, key):
        if key in ("y", "Y"):
            self._callback("y")
            return None
        if key in ("n", "N"):
            self._callback("n")
            return None
        if key in ("c", "C", "esc"):
            self._callback("c")
            return None
        return key

    def selectable(self) -> bool:
        return True
