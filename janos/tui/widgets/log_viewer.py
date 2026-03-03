"""Auto-scrolling log viewer for portal / evil twin monitoring."""

import urwid


class LogViewer(urwid.WidgetWrap):
    """Scrollable log that auto-scrolls to the bottom on new entries."""

    def __init__(self, max_lines: int = 500) -> None:
        self._max = max_lines
        self._walker = urwid.SimpleFocusListWalker([])
        self._listbox = urwid.ListBox(self._walker)
        super().__init__(self._listbox)

    def append(self, line: str, attr: str = "default") -> None:
        self._walker.append(urwid.Text((attr, f"  {line}")))
        if len(self._walker) > self._max:
            del self._walker[0]
        # Auto-scroll to bottom
        self._listbox.set_focus(len(self._walker) - 1)

    def clear(self) -> None:
        self._walker.clear()

    @property
    def line_count(self) -> int:
        return len(self._walker)
