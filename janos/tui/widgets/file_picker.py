"""File picker widget for HTML file selection."""

import urwid


class FilePickerItem(urwid.WidgetWrap):
    def __init__(self, name: str, index: int, selected: bool = False) -> None:
        self.name = name
        self.index = index
        mark = ">" if selected else " "
        attr = "table_row_sel" if selected else "table_row"
        text = urwid.Text(f" {mark} {index + 1}. {name}")
        widget = urwid.AttrMap(text, attr, focus_map="table_row_sel")
        super().__init__(widget)

    def selectable(self) -> bool:
        return True

    def keypress(self, size, key):
        return key


class FilePicker(urwid.WidgetWrap):
    """Selectable list of files. Calls callback(index, name) on Enter."""

    def __init__(self, files: list[str], callback, title: str = "Select HTML file:") -> None:
        self._files = files
        self._callback = callback
        self._selected = -1
        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        self._rebuild()

        title = urwid.Text(("dialog_title", f"  {title}"))
        hint = urwid.Text(("dim", "  [Enter] Select  [Esc] Cancel"))
        pile = urwid.Pile([
            ("pack", title),
            ("pack", urwid.Divider()),
            self._listbox,
            ("pack", urwid.Divider()),
            ("pack", hint),
        ])
        super().__init__(pile)

    def _rebuild(self) -> None:
        self._walker.clear()
        for i, f in enumerate(self._files):
            self._walker.append(FilePickerItem(f, i, i == self._selected))

    def keypress(self, size, key):
        if key == "enter":
            focus_w, idx = self._listbox.get_focus()
            if focus_w is not None and idx is not None:
                self._selected = idx
                self._callback(idx, self._files[idx])
            return None
        if key == "esc":
            self._callback(-1, "")
            return None
        return super().keypress(size, key)
