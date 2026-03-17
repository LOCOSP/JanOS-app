"""Scrollable list picker dialog overlay."""

import urwid


class ListPickerDialog(urwid.WidgetWrap):
    """Modal overlay with a scrollable list of choices.

    callback(index) on Enter, callback(None) on Esc.
    Arrow keys to navigate, Enter to select.
    """

    def __init__(self, title: str, choices: list[str], callback) -> None:
        self._callback = callback
        self._choices = choices

        buttons = []
        for i, label in enumerate(choices):
            btn = urwid.Button(f" {label}")
            urwid.connect_signal(btn, "click", self._on_click, user_args=[i])
            buttons.append(urwid.AttrMap(btn, "dialog", focus_map="dialog_title"))

        walker = urwid.SimpleFocusListWalker(buttons)
        listbox = urwid.ListBox(walker)

        header = urwid.Text(("dialog_title", f"  {title}"))
        frame = urwid.Frame(listbox, header=header)
        box = urwid.LineBox(frame, title="Select")
        widget = urwid.AttrMap(box, "dialog")
        super().__init__(widget)

    def _on_click(self, idx, _button):
        self._callback(idx)

    def keypress(self, size, key):
        if key in ("esc", "q"):
            self._callback(None)
            return None
        if key == "enter":
            # Get focused index
            frame = self._w.original_widget.original_widget
            listbox = frame.body
            focus_widget, idx = listbox.get_focus()
            if idx is not None:
                self._callback(idx)
                return None
        return super().keypress(size, key)

    def selectable(self) -> bool:
        return True
