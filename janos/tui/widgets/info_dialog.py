"""Simple informational overlay dismissed with any key."""

import urwid


class InfoDialog(urwid.WidgetWrap):
    """Modal overlay showing a message with [OK] hint. Calls *callback()* on dismiss."""

    def __init__(self, message: str, callback, title: str = "Info") -> None:
        self._callback = callback
        text = urwid.Text(
            ("dialog_title", f"\n  {message}\n\n  [OK] Press any key\n"),
            align="left",
        )
        fill = urwid.Filler(text, valign="middle")
        box = urwid.LineBox(fill, title=title)
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def keypress(self, size, key):
        self._callback()
        return None

    def selectable(self) -> bool:
        return True
