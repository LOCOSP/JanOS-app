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

# Map point type → urwid palette attribute
_TYPE_ATTR = {
    "handshake": "error",       # red
    "wifi":      "success",     # green
    "bt":        "bold",        # cyan
    "meshcore":  "warning",     # yellow
}


class BrailleCanvas:
    """Minimal braille canvas — set pixels, render to Unicode string."""

    def __init__(self, pixel_w: int, pixel_h: int) -> None:
        self.pw = pixel_w
        self.ph = pixel_h
        self.cw = (pixel_w + 1) // 2
        self.ch = (pixel_h + 3) // 4
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

    def get_char(self, col: int, row: int) -> str:
        """Get braille character for a given cell."""
        bits = self._cells.get((col, row), 0)
        return chr(0x2800 + bits)

    def frame(self) -> list[str]:
        """Render canvas to list of strings (one per character row)."""
        lines: list[str] = []
        for row in range(self.ch):
            chars: list[str] = []
            for col in range(self.cw):
                chars.append(self.get_char(col, row))
            lines.append("".join(chars))
        return lines


class BrailleMapWidget(urwid.Widget):
    """Urwid widget that renders a world map with GPS loot points."""

    _sizing = frozenset(["box"])
    _selectable = True  # must be selectable to receive keypresses

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

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        """Pass all keys up — MapScreen handles them."""
        return key

    # ── rendering ──

    def render(self, size: tuple[int, int], focus: bool = False) -> urwid.Canvas:  # type: ignore[override]
        cols, rows = size
        pw = cols * 2   # braille pixel width
        ph = rows * 4   # braille pixel height

        # --- Layer 1: coastline ---
        coast = BrailleCanvas(pw, ph)
        for segment in COASTLINES:
            for i in range(len(segment) - 1):
                lat0, lon0 = segment[i]
                lat1, lon1 = segment[i + 1]
                if abs(lon1 - lon0) > 180:
                    continue
                x0, y0 = self._lonlat_to_pixel(lon0, lat0, pw, ph)
                x1, y1 = self._lonlat_to_pixel(lon1, lat1, pw, ph)
                coast.line(x0, y0, x1, y1)

        # --- Layer 2: GPS points (separate canvas + cell tracking) ---
        points_canvas = BrailleCanvas(pw, ph)
        # Track which character cells have points and their type
        # (col, row) -> attr name (priority: last written wins)
        point_cells: dict[tuple[int, int], str] = {}

        for pt in self._points:
            if not self._filters.get(pt.get("type", ""), True):
                continue
            lat = pt.get("lat", 0.0)
            lon = pt.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                continue
            ptype = pt.get("type", "handshake")
            attr = _TYPE_ATTR.get(ptype, "error")
            px, py = self._lonlat_to_pixel(lon, lat, pw, ph)

            # Draw a larger marker (5×5 diamond) for visibility
            offsets = [
                (0, 0),
                (-1, 0), (1, 0), (0, -1), (0, 1),       # cross
                (-2, 0), (2, 0), (0, -2), (0, 2),        # extended cross
                (-1, -1), (1, -1), (-1, 1), (1, 1),      # diamond corners
            ]
            for dx, dy in offsets:
                points_canvas.set(px + dx, py + dy)

            # Mark character cells touched by this point
            for dx, dy in offsets:
                cpx, cpy = px + dx, py + dy
                if 0 <= cpx < pw and 0 <= cpy < ph:
                    ccol = cpx // 2
                    crow = cpy // 4
                    point_cells[(ccol, crow)] = attr

        # --- Merge layers and build TextCanvas ---
        text_rows: list[bytes] = []
        attr_rows: list[list[tuple[str | None, int]]] = []

        for row in range(min(coast.ch, rows)):
            chars: list[str] = []
            row_attrs: list[tuple[str | None, int]] = []

            for col in range(min(coast.cw, cols)):
                # Merge coastline + point bits
                coast_bits = coast._cells.get((col, row), 0)
                point_bits = points_canvas._cells.get((col, row), 0)
                merged = coast_bits | point_bits
                ch = chr(0x2800 + merged)
                char_bytes = ch.encode("utf-8")

                # Determine attribute: point color if has points, else dim
                if (col, row) in point_cells:
                    cell_attr = point_cells[(col, row)]
                else:
                    cell_attr = "dim"

                chars.append(ch)
                # Build per-character attributes
                if row_attrs and row_attrs[-1][0] == cell_attr:
                    # Extend previous run
                    prev_attr, prev_len = row_attrs[-1]
                    row_attrs[-1] = (prev_attr, prev_len + len(char_bytes))
                else:
                    row_attrs.append((cell_attr, len(char_bytes)))

            encoded = "".join(chars).encode("utf-8")
            text_rows.append(encoded)
            attr_rows.append(row_attrs if row_attrs else [(None, 0)])

        # Pad if fewer lines than rows
        while len(text_rows) < rows:
            text_rows.append(b"")
            attr_rows.append([(None, 0)])

        return urwid.TextCanvas(text_rows, attr_rows)

    def rows(self, size: tuple[int, ...], focus: bool = False) -> int:
        return size[1] if len(size) > 1 else 20
