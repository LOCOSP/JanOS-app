"""Modal text input dialog overlay."""

import urwid


class TextInputDialog(urwid.WidgetWrap):
    """Single-line text input dialog. Calls callback(text) on Enter, callback(None) on Esc."""

    def __init__(self, prompt: str, callback, initial: str = "") -> None:
        self._callback = callback
        self._edit = urwid.Edit(("dialog_title", f"  {prompt}: "), initial)
        hint = urwid.Text(("dim", "  [Enter] Confirm  [Esc] Cancel"))
        pile = urwid.Pile([
            urwid.Divider(),
            self._edit,
            urwid.Divider(),
            hint,
        ])
        fill = urwid.Filler(pile, valign="middle")
        box = urwid.LineBox(fill, title="Input")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def keypress(self, size, key):
        if key == "enter":
            self._callback(self._edit.get_edit_text())
            return None
        if key == "esc":
            self._callback(None)
            return None
        return super().keypress(size, key)

    def selectable(self) -> bool:
        return True
