"""Braille-character world map widget for urwid.

Renders a vector world map using Unicode braille characters (U+2800-U+28FF).
Each terminal character cell represents a 2×4 pixel grid, so an 80-column
terminal gives 160 "pixels" wide and a 20-row map area gives 80 pixels tall.

No external dependencies — braille encoding is done inline.
"""

import urwid

from .coastline import COASTLINES

# Braille dot positions within a 2×4 cell:
#   (0,0) (1,0)   bit 0  bit 3
#   (0,1) (1,1)   bit 1  bit 4
#   (0,2) (1,2)   bit 2  bit 5
#   (0,3) (1,3)   bit 6  bit 7
_DOT_BITS = {
    (0, 0): 0x01, (1, 0): 0x08,
    (0, 1): 0x02, (1, 1): 0x10,
    (0, 2): 0x04, (1, 2): 0x20,
    (0, 3): 0x40, (1, 3): 0x80,
}


class BrailleCanvas:
    """Minimal braille canvas — set pixels, render to Unicode string."""

    def __init__(self, pixel_w: int, pixel_h: int) -> None:
        # Pixel dimensions (should be multiples of 2 and 4)
        self.pw = pixel_w
        self.ph = pixel_h
        # Character grid dimensions
        self.cw = (pixel_w + 1) // 2
        self.ch = (pixel_h + 3) // 4
        # Storage: dict of (char_col, char_row) -> bitmask
        self._cells: dict[tuple[int, int], int] = {}

    def set(self, px: int, py: int) -> None:
        """Set a single pixel."""
        if px < 0 or py < 0 or px >= self.pw or py >= self.ph:
            return
        col, ox = divmod(px, 2)
        row, oy = divmod(py, 4)
        bit = _DOT_BITS.get((ox, oy), 0)
        key = (col, row)
        self._cells[key] = self._cells.get(key, 0) | bit

    def line(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Draw a line using Bresenham's algorithm."""
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.set(x0, y0)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def frame(self) -> list[str]:
        """Render canvas to list of strings (one per character row)."""
        lines: list[str] = []
        for row in range(self.ch):
            chars: list[str] = []
            for col in range(self.cw):
                bits = self._cells.get((col, row), 0)
                chars.append(chr(0x2800 + bits))
            lines.append("".join(chars))
        return lines


class BrailleMapWidget(urwid.Widget):
    """Urwid widget that renders a world map with GPS loot points."""

    _sizing = frozenset(["box"])
    _selectable = False

    def __init__(self) -> None:
        super().__init__()
        self._points: list[dict] = []
        self._filters: dict[str, bool] = {
            "handshake": True,
            "wifi": True,
            "bt": True,
            "meshcore": True,
        }

    def set_points(self, points: list[dict]) -> None:
        """Update GPS points.  Each dict: {lat, lon, type, label}."""
        self._points = points
        self._invalidate()

    def toggle_filter(self, point_type: str) -> bool:
        """Toggle visibility of a point type.  Returns new state."""
        self._filters[point_type] = not self._filters.get(point_type, True)
        self._invalidate()
        return self._filters[point_type]

    def get_filter(self, point_type: str) -> bool:
        return self._filters.get(point_type, True)

    # ── projection helpers ──

    @staticmethod
    def _lonlat_to_pixel(
        lon: float, lat: float, pw: int, ph: int,
    ) -> tuple[int, int]:
        """Equirectangular projection: lon/lat → pixel coords."""
        x = int((lon + 180.0) / 360.0 * pw)
        y = int((90.0 - lat) / 180.0 * ph)
        return max(0, min(pw - 1, x)), max(0, min(ph - 1, y))

    # ── rendering ──

    def render(self, size: tuple[int, int], focus: bool = False) -> urwid.Canvas:  # type: ignore[override]
        cols, rows = size
        pw = cols * 2   # braille pixel width
        ph = rows * 4   # braille pixel height

        canvas = BrailleCanvas(pw, ph)

        # Draw coastlines
        for segment in COASTLINES:
            for i in range(len(segment) - 1):
                lat0, lon0 = segment[i]
                lat1, lon1 = segment[i + 1]
                # Skip lines that wrap around the date line
                if abs(lon1 - lon0) > 180:
                    continue
                x0, y0 = self._lonlat_to_pixel(lon0, lat0, pw, ph)
                x1, y1 = self._lonlat_to_pixel(lon1, lat1, pw, ph)
                canvas.line(x0, y0, x1, y1)

        # Overlay GPS points (as 3×3 bright dots for visibility)
        for pt in self._points:
            if not self._filters.get(pt.get("type", ""), True):
                continue
            lat = pt.get("lat", 0.0)
            lon = pt.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                continue
            px, py = self._lonlat_to_pixel(lon, lat, pw, ph)
            # Draw a small cross for visibility
            for dx in (-1, 0, 1):
                canvas.set(px + dx, py)
            canvas.set(px, py - 1)
            canvas.set(px, py + 1)

        # Convert to urwid TextCanvas
        braille_lines = canvas.frame()

        # Build attributed text rows
        text_rows: list[bytes] = []
        attr_rows: list[list[tuple[str | None, int]]] = []

        for line in braille_lines[:rows]:
            encoded = line[:cols].encode("utf-8")
            text_rows.append(encoded)
            attr_rows.append([("dim", len(encoded))])

        # Pad if fewer lines than rows
        while len(text_rows) < rows:
            text_rows.append(b"")
            attr_rows.append([(None, 0)])

        return urwid.TextCanvas(text_rows, attr_rows)

    def rows(self, size: tuple[int, ...], focus: bool = False) -> int:
        return size[1] if len(size) > 1 else 20
