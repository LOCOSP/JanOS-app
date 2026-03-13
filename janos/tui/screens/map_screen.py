"""Map screen — braille world map with GPS-tagged loot points."""

import urwid

from ...app_state import AppState
from ...loot_manager import LootManager
from ..widgets.braille_map import BrailleMapWidget


class MapScreen(urwid.WidgetWrap):
    """Tab 5 — vector world map showing GPS-tagged loot locations."""

    def __init__(self, state: AppState, loot: LootManager) -> None:
        self.state = state
        self.loot = loot

        self._map = BrailleMapWidget()
        self._last_refresh = 0.0

        # Info bar (point counts + viewport info)
        self._info = urwid.Text(("dim", "  Map  Loading..."))

        # Legend bar
        self._legend = urwid.Text(self._build_legend())

        # Key hints
        self._keys = urwid.Text(
            ("dim", "  [↑↓←→] Pan  [+/-] Zoom  [0] World  [c] Center  [r] Refresh"),
        )

        pile = urwid.Pile([
            ("pack", self._info),
            self._map,
            ("pack", self._legend),
            ("pack", self._keys),
        ])

        super().__init__(pile)
        self._load_points()

    def selectable(self) -> bool:
        return True

    # ── legend ──

    def _build_legend(self) -> list:
        """Build colored legend with filter state indicators."""
        parts: list = [("dim", "  ")]
        filters = [
            ("handshake", "error", "HS"),
            ("wifi", "success", "WiFi"),
            ("bt", "bold", "BT"),
            ("meshcore", "warning", "MC"),
        ]
        for ptype, attr, label in filters:
            on = self._map.get_filter(ptype)
            marker = "●" if on else "○"
            parts.append((attr if on else "dim", f" {marker} {label} "))
        return parts

    # ── info bar ──

    def _update_info(self) -> None:
        """Update the info bar with point counts and viewport info."""
        points = self._map._points
        counts: dict[str, int] = {}
        for pt in points:
            t = pt.get("type", "?")
            counts[t] = counts.get(t, 0) + 1

        total = len(points)
        parts = []
        if counts.get("handshake"):
            parts.append(f"HS:{counts['handshake']}")
        if counts.get("wifi"):
            parts.append(f"WiFi:{counts['wifi']}")
        if counts.get("bt"):
            parts.append(f"BT:{counts['bt']}")
        if counts.get("meshcore"):
            parts.append(f"MC:{counts['meshcore']}")

        summary = " │ ".join(parts) if parts else "No GPS data"

        # Viewport info
        zoom_label = self._map.get_zoom_label()
        lat = self._map.center_lat
        lon = self._map.center_lon
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        coord_str = f"{abs(lat):.1f}°{ns} {abs(lon):.1f}°{ew}"

        if self._map.zoom == 0:
            view_str = f"[{zoom_label}]"
        else:
            view_str = f"[{zoom_label} z{self._map.zoom}] {coord_str}"

        self._info.set_text(
            ("dim", f"  Map  {total} pts  ({summary})  {view_str}")
        )

    # ── data loading ──

    def _load_points(self) -> None:
        """Load GPS points from loot manager."""
        import time
        self._last_refresh = time.monotonic()

        points = self.loot.get_gps_points()
        self._map.set_points(points)
        self._update_info()

    # ── public API ──

    def refresh(self) -> None:
        """Called by app main loop — auto-reload every 30s."""
        import time
        if time.monotonic() - self._last_refresh > 30.0:
            self._load_points()

    def handle_serial_line(self, line: str) -> None:
        """Map screen doesn't process serial data."""
        pass

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        # Filter toggles
        if key == "h":
            self._map.toggle_filter("handshake")
            self._legend.set_text(self._build_legend())
            return None
        if key == "w":
            self._map.toggle_filter("wifi")
            self._legend.set_text(self._build_legend())
            return None
        if key == "b":
            self._map.toggle_filter("bt")
            self._legend.set_text(self._build_legend())
            return None
        if key == "m":
            self._map.toggle_filter("meshcore")
            self._legend.set_text(self._build_legend())
            return None
        if key == "r":
            self._load_points()
            return None

        # Navigation keys — delegate to map widget
        if key in ("up", "down", "left", "right", "+", "=", "-", "_", "0", "c"):
            result = self._map.keypress(size, key)
            self._update_info()
            return result

        return key
