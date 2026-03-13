"""Braille-character world map widget for urwid.

Renders a vector world map using Unicode braille characters (U+2800-U+28FF).
Each terminal character cell represents a 2×4 pixel grid, so an 80-column
terminal gives 160 "pixels" wide and a 20-row map area gives 80 pixels tall.

Supports pan (arrow keys) and zoom (+/-) with viewport state.

No external dependencies — braille encoding is done inline.
"""

import math
import time

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

# Zoom levels: (lon_span, lat_span) — degrees visible on screen
_ZOOM_LEVELS = [
    (360.0, 155.0),   # 0 — whole world (cropped poles: -65°S to 90°N)
    (180.0, 90.0),    # 1
    (90.0, 45.0),     # 2
    (45.0, 22.5),     # 3
    (22.5, 11.25),    # 4
    (10.0, 5.0),      # 5
    (5.0, 2.5),       # 6
    (2.0, 1.0),       # 7 — city level
    (1.0, 0.5),       # 8 — district level
    (0.5, 0.25),      # 9 — neighborhood
]

# Pan step as fraction of current viewport span
_PAN_FRACTION = 0.25


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
        # Viewport state
        self._zoom = 0          # index into _ZOOM_LEVELS
        self._center_lon = 0.0  # degrees
        self._center_lat = 12.5 # slightly north for better world view

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

    # ── viewport control ──

    @property
    def zoom(self) -> int:
        return self._zoom

    @property
    def center_lon(self) -> float:
        return self._center_lon

    @property
    def center_lat(self) -> float:
        return self._center_lat

    def zoom_in(self) -> bool:
        """Zoom in one level.  Returns True if zoom changed."""
        if self._zoom < len(_ZOOM_LEVELS) - 1:
            self._zoom += 1
            self._invalidate()
            return True
        return False

    def zoom_out(self) -> bool:
        """Zoom out one level.  Returns True if zoom changed."""
        if self._zoom > 0:
            self._zoom -= 1
            self._clamp_center()
            self._invalidate()
            return True
        return False

    def pan(self, dlat: float = 0.0, dlon: float = 0.0) -> None:
        """Pan the viewport by the given delta degrees."""
        self._center_lon += dlon
        self._center_lat += dlat
        self._clamp_center()
        self._invalidate()

    def pan_step(self, direction: str) -> None:
        """Pan by one step in the given direction (up/down/left/right)."""
        lon_span, lat_span = _ZOOM_LEVELS[self._zoom]
        step_lon = lon_span * _PAN_FRACTION
        step_lat = lat_span * _PAN_FRACTION
        if direction == "up":
            self.pan(dlat=step_lat)
        elif direction == "down":
            self.pan(dlat=-step_lat)
        elif direction == "left":
            self.pan(dlon=-step_lon)
        elif direction == "right":
            self.pan(dlon=step_lon)

    def reset_view(self) -> None:
        """Reset to world view."""
        self._zoom = 0
        self._center_lon = 0.0
        self._center_lat = 12.5
        self._invalidate()

    def center_on(self, lat: float, lon: float, zoom: int | None = None) -> None:
        """Center the map on a specific coordinate."""
        self._center_lat = lat
        self._center_lon = lon
        if zoom is not None:
            self._zoom = max(0, min(len(_ZOOM_LEVELS) - 1, zoom))
        self._clamp_center()
        self._invalidate()

    def center_on_points(self) -> bool:
        """Center on the centroid of visible GPS points. Returns True if moved."""
        visible = []
        for pt in self._points:
            if not self._filters.get(pt.get("type", ""), True):
                continue
            lat = pt.get("lat", 0.0)
            lon = pt.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                continue
            visible.append((lat, lon))
        if not visible:
            return False
        avg_lat = sum(p[0] for p in visible) / len(visible)
        avg_lon = sum(p[1] for p in visible) / len(visible)
        self._center_lat = avg_lat
        self._center_lon = avg_lon
        # Auto-zoom: find bounding box and pick zoom level
        min_lat = min(p[0] for p in visible)
        max_lat = max(p[0] for p in visible)
        min_lon = min(p[1] for p in visible)
        max_lon = max(p[1] for p in visible)
        span_lat = max(max_lat - min_lat, 0.01)
        span_lon = max(max_lon - min_lon, 0.01)
        # Find smallest zoom that fits, with 20% margin
        best = 0
        for i, (zlon, zlat) in enumerate(_ZOOM_LEVELS):
            if zlon >= span_lon * 1.2 and zlat >= span_lat * 1.2:
                best = i
            else:
                break
        self._zoom = best
        self._clamp_center()
        self._invalidate()
        return True

    def _clamp_center(self) -> None:
        """Clamp center so viewport doesn't go beyond world bounds."""
        lon_span, lat_span = _ZOOM_LEVELS[self._zoom]
        half_lon = lon_span / 2.0
        half_lat = lat_span / 2.0
        # Longitude wraps but we keep it simple — clamp to -180..180
        self._center_lon = max(-180.0 + half_lon,
                               min(180.0 - half_lon, self._center_lon))
        # Latitude: -90..90
        self._center_lat = max(-90.0 + half_lat,
                               min(90.0 - half_lat, self._center_lat))

    def _get_viewport(self) -> tuple[float, float, float, float]:
        """Return (min_lon, max_lon, min_lat, max_lat) for current viewport."""
        lon_span, lat_span = _ZOOM_LEVELS[self._zoom]
        half_lon = lon_span / 2.0
        half_lat = lat_span / 2.0
        return (
            self._center_lon - half_lon,
            self._center_lon + half_lon,
            self._center_lat - half_lat,
            self._center_lat + half_lat,
        )

    def get_zoom_label(self) -> str:
        """Return human-readable zoom label for status bar."""
        if self._zoom == 0:
            return "World"
        lon_span, _ = _ZOOM_LEVELS[self._zoom]
        if lon_span >= 90:
            return "Continent"
        if lon_span >= 20:
            return "Region"
        if lon_span >= 5:
            return "Country"
        if lon_span >= 1:
            return "City"
        if lon_span >= 0.25:
            return "District"
        return "Street"

    # ── projection helpers ──

    def _lonlat_to_pixel(
        self, lon: float, lat: float, pw: int, ph: int,
        vp: tuple[float, float, float, float],
    ) -> tuple[int, int]:
        """Equirectangular projection: lon/lat → pixel coords within viewport."""
        min_lon, max_lon, min_lat, max_lat = vp
        lon_span = max_lon - min_lon
        lat_span = max_lat - min_lat
        if lon_span == 0 or lat_span == 0:
            return 0, 0
        x = int((lon - min_lon) / lon_span * pw)
        y = int((max_lat - lat) / lat_span * ph)
        return max(0, min(pw - 1, x)), max(0, min(ph - 1, y))

    def _in_viewport(
        self, lon: float, lat: float,
        vp: tuple[float, float, float, float],
        margin: float = 0.0,
    ) -> bool:
        """Check if a point is within the viewport (with optional margin)."""
        min_lon, max_lon, min_lat, max_lat = vp
        return (min_lon - margin <= lon <= max_lon + margin and
                min_lat - margin <= lat <= max_lat + margin)

    def twinkle(self) -> None:
        """Called periodically to trigger redraw for point blinking."""
        if self._points:
            self._invalidate()

    def _pixel_visible(self, px: int, py: int) -> bool:
        """Determine if a single braille pixel should be lit this frame.

        Each pixel gets a unique phase from its absolute position.
        Two slow sine waves create organic twinkling — individual dots
        in a marker blink independently, like city lights at night.
        ~80-85% of pixels are 'on' at any moment.
        """
        phase = (px * 137.03 + py * 59.97) % 1.0 * (2.0 * math.pi)
        t = time.monotonic()
        wave1 = math.sin(t * 0.7 + phase)            # ~9s period
        wave2 = math.sin(t * 0.43 + phase * 2.13)    # ~15s period
        combined = (wave1 + wave2) / 2.0
        return combined > -0.35

    def keypress(self, size: tuple[int, int], key: str) -> str | None:
        """Handle navigation keys directly in widget."""
        if key == "up":
            self.pan_step("up")
            return None
        if key == "down":
            self.pan_step("down")
            return None
        if key == "left":
            self.pan_step("left")
            return None
        if key == "right":
            self.pan_step("right")
            return None
        if key in ("+", "="):
            self.zoom_in()
            return None
        if key in ("-", "_"):
            self.zoom_out()
            return None
        if key == "0":
            self.reset_view()
            return None
        if key == "c":
            self.center_on_points()
            return None
        return key

    # ── rendering ──

    def render(self, size: tuple[int, int], focus: bool = False) -> urwid.Canvas:  # type: ignore[override]
        cols, rows = size
        pw = cols * 2   # braille pixel width
        ph = rows * 4   # braille pixel height

        vp = self._get_viewport()
        min_lon, max_lon, min_lat, max_lat = vp
        lon_span = max_lon - min_lon
        lat_span = max_lat - min_lat
        # Margin in degrees for segment clipping (include nearby off-screen segments)
        clip_margin = max(lon_span, lat_span) * 0.1

        # --- Layer 1: coastline ---
        coast = BrailleCanvas(pw, ph)
        for segment in COASTLINES:
            for i in range(len(segment) - 1):
                lat0, lon0 = segment[i]
                lat1, lon1 = segment[i + 1]
                # Skip antimeridian-crossing segments
                if abs(lon1 - lon0) > 180:
                    continue
                # Clip: skip segments entirely outside viewport
                seg_min_lon = min(lon0, lon1)
                seg_max_lon = max(lon0, lon1)
                seg_min_lat = min(lat0, lat1)
                seg_max_lat = max(lat0, lat1)
                if (seg_max_lon < min_lon - clip_margin or
                    seg_min_lon > max_lon + clip_margin or
                    seg_max_lat < min_lat - clip_margin or
                    seg_min_lat > max_lat + clip_margin):
                    continue
                x0, y0 = self._lonlat_to_pixel(lon0, lat0, pw, ph, vp)
                x1, y1 = self._lonlat_to_pixel(lon1, lat1, pw, ph, vp)
                coast.line(x0, y0, x1, y1)

        # --- Layer 2: GPS points (separate canvas + cell tracking) ---
        points_canvas = BrailleCanvas(pw, ph)
        # Track which character cells have points and their type
        # (col, row) -> attr name (priority: last written wins)
        point_cells: dict[tuple[int, int], str] = {}

        # Marker size scales with zoom — bigger at higher zoom for visibility
        if self._zoom <= 1:
            marker_offsets = [
                (0, 0),
                (-1, 0), (1, 0), (0, -1), (0, 1),
                (-2, 0), (2, 0), (0, -2), (0, 2),
                (-1, -1), (1, -1), (-1, 1), (1, 1),
            ]
        elif self._zoom <= 4:
            marker_offsets = [
                (0, 0),
                (-1, 0), (1, 0), (0, -1), (0, 1),
                (-2, 0), (2, 0), (0, -2), (0, 2),
                (-1, -1), (1, -1), (-1, 1), (1, 1),
                (-3, 0), (3, 0), (0, -3), (0, 3),
                (-2, -1), (2, -1), (-2, 1), (2, 1),
                (-1, -2), (1, -2), (-1, 2), (1, 2),
            ]
        else:
            # Big markers at city/district zoom
            marker_offsets = []
            for dx in range(-4, 5):
                for dy in range(-4, 5):
                    if abs(dx) + abs(dy) <= 5:
                        marker_offsets.append((dx, dy))

        for pt in self._points:
            if not self._filters.get(pt.get("type", ""), True):
                continue
            lat = pt.get("lat", 0.0)
            lon = pt.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                continue
            # Skip points outside viewport
            if not self._in_viewport(lon, lat, vp, margin=clip_margin):
                continue
            ptype = pt.get("type", "handshake")
            attr = _TYPE_ATTR.get(ptype, "error")
            px, py = self._lonlat_to_pixel(lon, lat, pw, ph, vp)

            # Per-pixel twinkle: each dot in the marker blinks independently
            for dx, dy in marker_offsets:
                cpx, cpy = px + dx, py + dy
                if 0 <= cpx < pw and 0 <= cpy < ph:
                    if self._pixel_visible(cpx, cpy):
                        points_canvas.set(cpx, cpy)
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
