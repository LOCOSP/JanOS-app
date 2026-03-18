"""JanOS // WATCH_MODE — Open World Hacking RPG.

Watch Dogs meets Pokemon Go: real GPS tracking on a world map
with coastline rendering, BLE/WiFi scanning, handshake capture.

Usage:
  python3 -m janos.game.watchdogs                    # standalone
  python3 -m janos.game.watchdogs /dev/ttyUSB0       # with ESP32
  python3 -m janos.game.watchdogs /dev/ttyUSB0 /path/to/loot

Controls:
  Arrow keys — move (overrides GPS when no fix)
  [1] WiFi Wardriving  [2] BT Wardriving  [3] Packet Sniffer
  [4] Handshake Capture  [5] Handshake No SD
  [b] BLE Scan  [SPACE] Hack nearby device
  [+/-] Zoom in/out  [c] Center on player  [0] World view
  [ESC] Quit
"""

import json
import math
import os
import random
import sys
import time
from pathlib import Path

import pyxel

# ---------------------------------------------------------------------------
# Coastline data import
# ---------------------------------------------------------------------------
try:
    from janos.tui.widgets.coastline import COASTLINES
except ImportError:
    # Standalone mode — try relative
    _here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_here.parent))
    try:
        from janos.tui.widgets.coastline import COASTLINES
    except ImportError:
        COASTLINES = []

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
W, H = 480, 270
FPS = 30
STATE_FILE = "/tmp/janos_state.json"   # JanOS → game (full state)
CMD_FILE = "/tmp/janos_game_cmd.txt"   # game → JanOS (commands)

# Colors (pyxel palette indices)
C_WATER = 1       # dark blue
C_LAND = 3        # dark green/teal
C_COAST = 5       # gray
C_GRID = 1        # dark
C_PLAYER = 7      # white
C_PLAYER_BODY = 12  # blue
C_PLAYER_HOOD = 5   # gray
C_HACK_GREEN = 11   # green
C_HACK_CYAN = 3     # cyan/teal
C_HUD_BG = 0      # black
C_HUD_LINE = 1    # dark blue
C_TEXT = 7         # white
C_DIM = 13        # dim gray
C_WARNING = 10     # yellow
C_ERROR = 8        # red
C_SUCCESS = 11     # green

# Zoom levels: (degrees_lon_span, label)
ZOOM_LEVELS = [
    (360.0, "WORLD"),
    (180.0, "CONTINENT"),
    (90.0, "REGION"),
    (45.0, "COUNTRY"),
    (20.0, "AREA"),
    (10.0, "CITY"),
    (5.0, "DISTRICT"),
    (2.0, "NEIGHBORHOOD"),
    (1.0, "STREET"),
    (0.5, "BLOCK"),
]

# Level names
LEVEL_NAMES = [
    "", "SCRIPT_KIDDIE", "SKIDDIE+", "HACKER",
    "NETRUNNER", "ELITE", "GHOST", "CYBER_GOD",
]

# ---------------------------------------------------------------------------
# IPC: read JanOS state, send commands
# ---------------------------------------------------------------------------

def read_janos_state() -> dict:
    """Read full JanOS state from shared JSON file."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def send_command(cmd: str) -> None:
    """Append a command for JanOS to execute."""
    try:
        with open(CMD_FILE, "a") as f:
            f.write(cmd + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Loot GPS points loader
# ---------------------------------------------------------------------------

def load_gps_markers(loot_path: str | None) -> list[dict]:
    """Load GPS markers from loot .gps.json sidecars."""
    markers = []
    if not loot_path:
        return markers
    loot_dir = Path(loot_path)
    if not loot_dir.is_dir():
        return markers
    for session in loot_dir.iterdir():
        if not session.is_dir():
            continue
        hs_dir = session / "handshakes"
        if not hs_dir.is_dir():
            continue
        for gps_file in hs_dir.glob("*.gps.json"):
            try:
                data = json.loads(gps_file.read_text())
                lat = data.get("Latitude", data.get("lat", 0))
                lon = data.get("Longitude", data.get("lon", 0))
                if lat and lon:
                    label = gps_file.name.replace(".gps.json", "")
                    # Extract SSID from filename pattern
                    parts = label.rsplit("_", 1)
                    ssid = parts[0] if len(parts) > 1 else label
                    markers.append({
                        "lat": float(lat),
                        "lon": float(lon),
                        "label": ssid[:20],
                        "type": "handshake",
                    })
            except Exception:
                continue
    return markers


# ---------------------------------------------------------------------------
# Game objects
# ---------------------------------------------------------------------------

class BleDevice:
    """A BLE device on the map."""

    def __init__(self, lat: float, lon: float, mac: str, name: str, rssi: int):
        self.lat, self.lon = lat, lon
        self.mac = mac
        self.name = name[:18]
        self.rssi = rssi
        self.hacked = False
        self.blink_phase = random.random() * 6.28
        self.spawn_frame = 0

    @property
    def color(self):
        if self.rssi > -45:
            return 11  # green
        if self.rssi > -60:
            return 10  # yellow
        if self.rssi > -75:
            return 9   # orange
        return 8       # red


class WifiNetwork:
    """A WiFi network on the map."""

    def __init__(self, lat: float, lon: float, bssid: str, ssid: str,
                 channel: int, rssi: int):
        self.lat, self.lon = lat, lon
        self.bssid = bssid
        self.ssid = ssid[:18]
        self.channel = channel
        self.rssi = rssi
        self.hacked = False
        self.spawn_frame = 0

    @property
    def color(self):
        if self.rssi > -50:
            return 11
        if self.rssi > -65:
            return 10
        if self.rssi > -80:
            return 9
        return 8


class Particle:
    """Visual effect particle."""

    def __init__(self, x: float, y: float, color: int = 3):
        self.x, self.y = x, y
        a = random.random() * 6.28
        s = random.random() * 2.5 + 0.5
        self.vx = math.cos(a) * s
        self.vy = math.sin(a) * s
        self.life = random.randint(10, 30)
        self.color = color


class MapMarker:
    """Persistent GPS marker (handshakes, etc.)."""

    def __init__(self, lat: float, lon: float, label: str, mtype: str):
        self.lat, self.lon = lat, lon
        self.label = label
        self.type = mtype


# ---------------------------------------------------------------------------
# Map projection
# ---------------------------------------------------------------------------

class MapProjection:
    """Equirectangular projection with zoom and pan."""

    def __init__(self):
        self.center_lat = 20.0
        self.center_lon = 0.0
        self.zoom = 0  # index into ZOOM_LEVELS
        self._target_lat = self.center_lat
        self._target_lon = self.center_lon

    @property
    def lon_span(self) -> float:
        return ZOOM_LEVELS[self.zoom][0]

    @property
    def lat_span(self) -> float:
        return self.lon_span * (H - 30) / W  # aspect ratio, minus HUD

    @property
    def label(self) -> str:
        return ZOOM_LEVELS[self.zoom][1]

    def smooth_move(self, lat: float, lon: float):
        self._target_lat = lat
        self._target_lon = lon

    def update(self):
        """Smooth camera follow."""
        self.center_lat += (self._target_lat - self.center_lat) * 0.08
        self.center_lon += (self._target_lon - self.center_lon) * 0.08

    def geo_to_screen(self, lat: float, lon: float) -> tuple[int, int]:
        """Convert lat/lon to screen coordinates."""
        # Map area (excluding HUD: top 12px, bottom 14px)
        map_h = H - 26
        map_y_offset = 12

        dx = lon - self.center_lon
        # Handle antimeridian wrap
        if dx > 180:
            dx -= 360
        elif dx < -180:
            dx += 360

        dy = self.center_lat - lat  # lat increases upward

        sx = int(W / 2 + dx * W / self.lon_span)
        sy = int(map_y_offset + map_h / 2 + dy * map_h / self.lat_span)
        return sx, sy

    def screen_visible(self, sx: int, sy: int) -> bool:
        return -20 <= sx <= W + 20 and -20 <= sy <= H + 20

    def zoom_in(self):
        if self.zoom < len(ZOOM_LEVELS) - 1:
            self.zoom += 1

    def zoom_out(self):
        if self.zoom > 0:
            self.zoom -= 1

    def reset_view(self):
        self.zoom = 0
        self._target_lat = 20.0
        self._target_lon = 0.0


# ---------------------------------------------------------------------------
# Main game
# ---------------------------------------------------------------------------

class WatchDogsGame:
    """JanOS // WATCH_MODE — Open World Hacking RPG."""

    def __init__(self, serial_port: str | None = None,
                 loot_path: str | None = None):
        pyxel.init(W, H, title="JanOS // WATCH_MODE", fps=FPS)
        pyxel.mouse(False)

        # Map
        self.proj = MapProjection()
        self._coastline_cache: list[list[tuple[float, float]]] = COASTLINES

        # Player
        self.player_lat = 51.1  # Default: Opole, Poland
        self.player_lon = 17.9
        self.player_dir = 0  # 0=down, 1=up, 2=left, 3=right
        self.walk_frame = 0
        self.gps_fix = False
        self._manual_move = False

        # Game state
        self.xp = 0
        self.level = 1
        self.ble_devices: list[BleDevice] = []
        self.wifi_networks: list[WifiNetwork] = []
        self.markers: list[MapMarker] = []
        self.particles: list[Particle] = []
        self.msgs: list[tuple[str, int, int]] = []  # (text, timer, color)
        self.scan_pulse = 0
        self.glitch_timer = 0
        self.scan_lines: list[int] = []
        self._known_ble_macs: set[str] = set()
        self._known_wifi_bssids: set[str] = set()

        # Hack state
        self.hack_target = None  # BleDevice or WifiNetwork
        self.hack_progress = 0
        self.hacking = False

        # Active operations (synced from JanOS state)
        self.wifi_scanning = False
        self.ble_scanning = False
        self.sniffing = False
        self.capturing_hs = False
        self._esp32_connected = False
        self._last_hs_count = 0

        # Clear command file
        try:
            open(CMD_FILE, "w").close()
        except Exception:
            pass

        # Load persistent markers from loot
        self.loot_path = loot_path
        loaded = load_gps_markers(loot_path)
        for m in loaded:
            self.markers.append(
                MapMarker(m["lat"], m["lon"], m["label"], m["type"]))
        if loaded:
            self.msg(f"[LOOT] {len(loaded)} handshake markers loaded", C_DIM)

        # Center camera on player
        self.proj.smooth_move(self.player_lat, self.player_lon)
        self.proj.zoom = 5  # city level

        self.msg("[SYS] JanOS // WATCH_MODE initialized", C_HACK_CYAN)
        self.msg("[SYS] Arrow keys=move [b]=scan [1-5]=tools", C_DIM)

        pyxel.run(self.update, self.draw)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def msg(self, text: str, color: int = C_TEXT):
        self.msgs.append((text, 180, color))
        if len(self.msgs) > 8:
            self.msgs.pop(0)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self):
        # Read JanOS state (GPS, devices, status)
        self._sync_janos_state()

        # Movement
        self._update_movement()

        # Camera
        self.proj.smooth_move(self.player_lat, self.player_lon)
        self.proj.update()

        # Scan pulse
        self.scan_pulse = (self.scan_pulse + 1) % 60

        # Hack
        self._update_hack()

        # Particles
        for p in self.particles:
            p.x += p.vx
            p.y += p.vy
            p.life -= 1
        self.particles = [p for p in self.particles if p.life > 0]

        # Messages
        self.msgs = [(t, tm - 1, c) for t, tm, c in self.msgs if tm > 1]

        # Glitch
        if self.glitch_timer > 0:
            self.glitch_timer -= 1
        if pyxel.frame_count % 15 == 0:
            self.scan_lines = [
                random.randint(0, H - 1)
                for _ in range(random.randint(0, 2))
            ]

        # Zoom
        if pyxel.btnp(pyxel.KEY_PLUS) or pyxel.btnp(pyxel.KEY_KP_PLUS):
            self.proj.zoom_in()
        if pyxel.btnp(pyxel.KEY_MINUS) or pyxel.btnp(pyxel.KEY_KP_MINUS):
            self.proj.zoom_out()
        if pyxel.btnp(pyxel.KEY_0):
            self.proj.reset_view()
        if pyxel.btnp(pyxel.KEY_C):
            self.proj.smooth_move(self.player_lat, self.player_lon)

        # Tool keys
        if pyxel.btnp(pyxel.KEY_1):
            self._toggle_wifi_scan()
        if pyxel.btnp(pyxel.KEY_2):
            self._toggle_bt_scan()
        if pyxel.btnp(pyxel.KEY_3):
            self._toggle_sniffer()
        if pyxel.btnp(pyxel.KEY_4):
            self._start_handshake(serial_mode=False)
        if pyxel.btnp(pyxel.KEY_5):
            self._start_handshake(serial_mode=True)
        if pyxel.btnp(pyxel.KEY_B):
            self._do_ble_scan()

        # Quit
        if pyxel.btnp(pyxel.KEY_ESCAPE):
            self._cleanup()
            pyxel.quit()

    def _sync_janos_state(self):
        """Read JanOS state from shared file — GPS, devices, attacks."""
        if pyxel.frame_count % 10 != 0:  # every ~330ms
            return
        state = read_janos_state()
        if not state:
            return

        # GPS
        lat = state.get("gps_lat", 0.0)
        lon = state.get("gps_lon", 0.0)
        fix = state.get("gps_fix", False)
        if fix and lat != 0 and lon != 0 and not self._manual_move:
            self.player_lat = lat
            self.player_lon = lon
            self.gps_fix = True
        else:
            self.gps_fix = fix

        # ESP32 status
        self._esp32_connected = state.get("esp32", False)
        self.wifi_scanning = state.get("wardriving", False)
        self.ble_scanning = state.get("bt_scanning", False)
        self.sniffing = state.get("sniffer", False)
        self.capturing_hs = state.get("handshake", False)

        # BLE devices from JanOS
        for dev in state.get("ble_devices", []):
            mac = dev.get("mac", "")
            if mac and mac not in self._known_ble_macs:
                self._known_ble_macs.add(mac)
                name = dev.get("name", "Unknown")[:18]
                rssi = dev.get("rssi", -70)
                offset_lat = (random.random() - 0.5) * self.proj.lat_span * 0.3
                offset_lon = (random.random() - 0.5) * self.proj.lon_span * 0.3
                d = BleDevice(
                    self.player_lat + offset_lat,
                    self.player_lon + offset_lon,
                    mac, name, rssi,
                )
                d.spawn_frame = pyxel.frame_count
                self.ble_devices.append(d)
                self.msg(f"[BLE] {name} {mac[-8:]} {rssi}dBm", C_HACK_CYAN)
                self.xp += 10

        # WiFi networks from JanOS
        for net in state.get("wifi_networks", []):
            bssid = net.get("bssid", "")
            if bssid and bssid not in self._known_wifi_bssids:
                self._known_wifi_bssids.add(bssid)
                ssid = net.get("ssid", "?")[:18]
                ch = net.get("channel", 0)
                rssi = net.get("rssi", -70)
                offset_lat = (random.random() - 0.5) * self.proj.lat_span * 0.2
                offset_lon = (random.random() - 0.5) * self.proj.lon_span * 0.2
                n = WifiNetwork(
                    self.player_lat + offset_lat,
                    self.player_lon + offset_lon,
                    bssid, ssid, ch, rssi,
                )
                n.spawn_frame = pyxel.frame_count
                self.wifi_networks.append(n)
                self.msg(f"[WiFi] {ssid} Ch:{ch} {rssi}dBm", C_WARNING)
                self.xp += 15

        # Handshake count — detect new captures
        hs_count = state.get("handshakes", 0)
        if hs_count > self._last_hs_count and self._last_hs_count > 0:
            new = hs_count - self._last_hs_count
            for _ in range(new):
                self.msg("[HS] Handshake captured!", C_SUCCESS)
                self.xp += 200
                self.glitch_timer = 10
                if self.player_lat and self.player_lon:
                    self.markers.append(MapMarker(
                        self.player_lat, self.player_lon,
                        "Handshake", "handshake",
                    ))
                    px, py = self.proj.geo_to_screen(
                        self.player_lat, self.player_lon)
                    for _ in range(30):
                        self.particles.append(
                            Particle(px, py, random.choice([11, 10, 3])))
        self._last_hs_count = hs_count

    def _update_movement(self):
        """Arrow key movement (manual override or no GPS)."""
        speed = self.proj.lon_span / W * 3  # proportional to zoom

        moved = False
        if pyxel.btn(pyxel.KEY_UP):
            self.player_lat += speed
            self.player_dir = 1
            moved = True
        if pyxel.btn(pyxel.KEY_DOWN):
            self.player_lat -= speed
            self.player_dir = 0
            moved = True
        if pyxel.btn(pyxel.KEY_LEFT):
            self.player_lon -= speed
            self.player_dir = 2
            moved = True
        if pyxel.btn(pyxel.KEY_RIGHT):
            self.player_lon += speed
            self.player_dir = 3
            moved = True

        if moved:
            self.walk_frame = (self.walk_frame + 1) % 16
            self._manual_move = True
        else:
            self.walk_frame = 0

        # Clamp
        self.player_lat = max(-85, min(85, self.player_lat))
        if self.player_lon > 180:
            self.player_lon -= 360
        elif self.player_lon < -180:
            self.player_lon += 360

    def _update_hack(self):
        """Handle hack mechanic."""
        if pyxel.btn(pyxel.KEY_SPACE):
            if not self.hacking:
                # Find closest unhacked target
                best = None
                best_dist = 999
                px, py = self.proj.geo_to_screen(
                    self.player_lat, self.player_lon)
                for d in self.ble_devices + self.wifi_networks:
                    if d.hacked:
                        continue
                    sx, sy = self.proj.geo_to_screen(d.lat, d.lon)
                    dist = math.hypot(sx - px, sy - py)
                    if dist < 30 and dist < best_dist:
                        best = d
                        best_dist = dist
                if best:
                    self.hacking = True
                    self.hack_target = best
                    self.hack_progress = 0
            elif self.hack_target:
                self.hack_progress += 1
                if pyxel.frame_count % 3 == 0:
                    self.glitch_timer = 2
                if self.hack_progress >= 45:
                    self.hack_target.hacked = True
                    self.hacking = False
                    self.xp += 50
                    name = getattr(self.hack_target, "name",
                                   getattr(self.hack_target, "ssid", "?"))
                    self.msg(f"[PWNED] {name}", C_SUCCESS)
                    # Particles at target screen pos
                    sx, sy = self.proj.geo_to_screen(
                        self.hack_target.lat, self.hack_target.lon)
                    for _ in range(20):
                        self.particles.append(Particle(sx, sy, C_SUCCESS))
                    self.hack_target = None
                    new_lvl = 1 + self.xp // 200
                    if new_lvl > self.level:
                        self.level = new_lvl
                        self.msg(
                            f"[LEVEL UP] Level {self.level}!",
                            C_WARNING)
        else:
            if self.hacking and self.hack_progress < 45:
                self.hacking = False
                self.hack_target = None

    # ------------------------------------------------------------------
    # Tools — send commands to JanOS via IPC
    # ------------------------------------------------------------------

    def _send_cmd(self, cmd: str):
        send_command(cmd)
        self.glitch_timer = 5

    def _toggle_wifi_scan(self):
        if not self._esp32_connected:
            self.msg("[ERR] No ESP32 connected", C_ERROR)
            return
        if self.wifi_scanning:
            self._send_cmd("stop")
            self.msg("[WiFi] Scan stopped", C_DIM)
        else:
            self._send_cmd("scan_networks")
            self.msg("[WiFi] Wardriving started...", C_WARNING)

    def _toggle_bt_scan(self):
        if not self._esp32_connected:
            self.msg("[ERR] No ESP32 connected", C_ERROR)
            return
        if self.ble_scanning:
            self._send_cmd("stop")
            self.msg("[BT] Scan stopped", C_DIM)
        else:
            self._send_cmd("bt_scan")
            self.msg("[BT] Wardriving started...", 12)

    def _toggle_sniffer(self):
        if not self._esp32_connected:
            self.msg("[ERR] No ESP32 connected", C_ERROR)
            return
        if self.sniffing:
            self._send_cmd("stop")
            self.msg("[SNF] Sniffer stopped", C_DIM)
        else:
            self._send_cmd("start_sniffer")
            self.msg("[SNF] Packet sniffer active...", 12)

    def _start_handshake(self, serial_mode: bool = False):
        if not self._esp32_connected:
            self.msg("[ERR] No ESP32 connected", C_ERROR)
            return
        if self.capturing_hs:
            self._send_cmd("stop")
            self.msg("[HS] Capture stopped", C_DIM)
        else:
            cmd = "start_handshake_serial" if serial_mode else "start_handshake"
            self._send_cmd(cmd)
            mode = "Serial" if serial_mode else "SD"
            self.msg(f"[HS] Handshake capture ({mode})...", C_SUCCESS)

    def _do_ble_scan(self):
        if not self._esp32_connected:
            # Simulate for demo without ESP32
            for _ in range(random.randint(2, 5)):
                mac = ":".join(f"{random.randint(0,255):02X}" for _ in range(6))
                names = ["iPhone", "Galaxy", "AirPods", "MacBook",
                         "JBL Flip", "Apple Watch", "Pixel", "Sony WH"]
                name = random.choice(names)
                rssi = random.randint(-85, -35)
                if mac not in self._known_ble_macs:
                    self._known_ble_macs.add(mac)
                    offset_lat = (random.random() - 0.5) * self.proj.lat_span * 0.25
                    offset_lon = (random.random() - 0.5) * self.proj.lon_span * 0.25
                    dev = BleDevice(
                        self.player_lat + offset_lat,
                        self.player_lon + offset_lon,
                        mac, name, rssi,
                    )
                    dev.spawn_frame = pyxel.frame_count
                    self.ble_devices.append(dev)
                    self.msg(f"[BLE] {name} {rssi}dBm", C_HACK_CYAN)
                    self.xp += 10
            self.glitch_timer = 5
            return
        self._send_cmd("bt_scan")
        self.msg("[BLE] Scan pulse...", 12)

    def _cleanup(self):
        """Signal JanOS that game exited."""
        try:
            os.remove(CMD_FILE)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self):
        pyxel.cls(C_WATER)

        self._draw_coastlines()
        self._draw_grid()
        self._draw_markers()
        self._draw_wifi_networks()
        self._draw_ble_devices()
        self._draw_scan_effects()
        self._draw_player()
        self._draw_particles()
        self._draw_hack_bar()
        self._draw_glitch()
        self._draw_hud_top()
        self._draw_hud_bottom()
        self._draw_messages()
        self._draw_radar()
        self._draw_scanlines()

    def _draw_coastlines(self):
        """Render world coastlines."""
        for segment in self._coastline_cache:
            if len(segment) < 2:
                continue
            # Quick visibility check — skip segments entirely off screen
            lats = [p[0] for p in segment]
            lons = [p[1] for p in segment]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)

            # Check if segment bbox overlaps view
            view_lat_min = self.proj.center_lat - self.proj.lat_span
            view_lat_max = self.proj.center_lat + self.proj.lat_span
            view_lon_min = self.proj.center_lon - self.proj.lon_span
            view_lon_max = self.proj.center_lon + self.proj.lon_span

            if (max_lat < view_lat_min or min_lat > view_lat_max):
                continue
            # Longitude wrap check is complex, skip for simple cases
            if (max_lon - min_lon) < 180:
                if (max_lon < view_lon_min or min_lon > view_lon_max):
                    continue

            # Draw filled polygon (simple scanline approach for small polygons)
            # For performance, just draw lines between consecutive points
            prev_sx, prev_sy = None, None
            for lat, lon in segment:
                sx, sy = self.proj.geo_to_screen(lat, lon)
                if prev_sx is not None:
                    # Skip antimeridian crossings
                    if abs(sx - prev_sx) < W * 0.8:
                        pyxel.line(prev_sx, prev_sy, sx, sy, C_LAND)
                prev_sx, prev_sy = sx, sy

            # Fill attempt: draw thicker coastlines at high zoom
            if self.proj.zoom >= 4 and len(segment) > 2:
                for lat, lon in segment:
                    sx, sy = self.proj.geo_to_screen(lat, lon)
                    if self.proj.screen_visible(sx, sy):
                        pyxel.pset(sx, sy, C_COAST)

    def _draw_grid(self):
        """Draw lat/lon grid lines."""
        if self.proj.zoom < 3:
            return
        # Grid spacing based on zoom
        spacing = max(0.1, self.proj.lon_span / 10)
        # Round to nice numbers
        for exp in [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30]:
            if exp >= spacing:
                spacing = exp
                break

        lat_start = int(
            (self.proj.center_lat - self.proj.lat_span) / spacing) * spacing
        lon_start = int(
            (self.proj.center_lon - self.proj.lon_span) / spacing) * spacing

        lat = lat_start
        while lat < self.proj.center_lat + self.proj.lat_span:
            sx0, sy = self.proj.geo_to_screen(lat, self.proj.center_lon - self.proj.lon_span)
            sx1, _ = self.proj.geo_to_screen(lat, self.proj.center_lon + self.proj.lon_span)
            if 12 < sy < H - 14:
                pyxel.line(max(0, sx0), sy, min(W - 1, sx1), sy, C_GRID)
            lat += spacing

        lon = lon_start
        while lon < self.proj.center_lon + self.proj.lon_span:
            sx, sy0 = self.proj.geo_to_screen(self.proj.center_lat + self.proj.lat_span, lon)
            _, sy1 = self.proj.geo_to_screen(self.proj.center_lat - self.proj.lat_span, lon)
            if 0 < sx < W:
                pyxel.line(sx, max(12, sy0), sx, min(H - 14, sy1), C_GRID)
            lon += spacing

    def _draw_markers(self):
        """Draw persistent GPS markers (handshakes)."""
        for m in self.markers:
            sx, sy = self.proj.geo_to_screen(m.lat, m.lon)
            if not self.proj.screen_visible(sx, sy):
                continue
            # Lock icon for handshakes
            if m.type == "handshake":
                # Outer glow
                if pyxel.frame_count % 30 < 20:
                    pyxel.circb(sx, sy, 4, C_ERROR)
                # Lock body
                pyxel.rect(sx - 2, sy - 1, 5, 4, C_ERROR)
                pyxel.rect(sx - 1, sy - 3, 3, 2, C_ERROR)
                pyxel.pset(sx, sy, C_WARNING)
                # Label
                if self.proj.zoom >= 4:
                    pyxel.text(sx + 5, sy - 3, m.label, C_ERROR)

    def _draw_wifi_networks(self):
        """Draw WiFi network nodes."""
        for net in self.wifi_networks:
            sx, sy = self.proj.geo_to_screen(net.lat, net.lon)
            if not self.proj.screen_visible(sx, sy):
                continue

            age = pyxel.frame_count - net.spawn_frame
            if age < 15:
                if age % 2 == 0:
                    pyxel.circb(sx, sy, 15 - age, C_WARNING)
                continue

            if net.hacked:
                # Diamond shape (hacked WiFi)
                pyxel.pset(sx, sy - 3, C_SUCCESS)
                pyxel.line(sx - 2, sy, sx, sy - 3, C_SUCCESS)
                pyxel.line(sx + 2, sy, sx, sy - 3, C_SUCCESS)
                pyxel.line(sx - 2, sy, sx, sy + 2, C_SUCCESS)
                pyxel.line(sx + 2, sy, sx, sy + 2, C_SUCCESS)
            else:
                # WiFi icon (concentric arcs)
                blink = math.sin(
                    pyxel.frame_count * 0.1 + hash(net.bssid) % 100)
                c = net.color if blink > 0 else 2
                pyxel.pset(sx, sy, c)
                if self.proj.zoom >= 4:
                    pyxel.circb(sx, sy, 3, c)
                if self.proj.zoom >= 5:
                    pyxel.circb(sx, sy, 5, c)

            # Label when close
            px, py = self.proj.geo_to_screen(
                self.player_lat, self.player_lon)
            if math.hypot(sx - px, sy - py) < 40 and self.proj.zoom >= 4:
                pyxel.text(sx - len(net.ssid) * 2, sy - 8, net.ssid, net.color)
                pyxel.text(sx - 8, sy + 5, f"{net.rssi}dBm", C_DIM)

    def _draw_ble_devices(self):
        """Draw BLE device nodes."""
        for d in self.ble_devices:
            sx, sy = self.proj.geo_to_screen(d.lat, d.lon)
            if not self.proj.screen_visible(sx, sy):
                continue

            age = pyxel.frame_count - d.spawn_frame
            if age < 15:
                if age % 2 == 0:
                    pyxel.circb(sx, sy, 15 - age, C_HACK_CYAN)
                continue

            if d.hacked:
                pyxel.rect(sx - 2, sy - 2, 5, 5, C_HACK_CYAN)
                pyxel.pset(sx, sy, C_SUCCESS)
                if self.proj.zoom >= 4:
                    pyxel.text(sx - len(d.name) * 2, sy - 7, d.name, C_HACK_CYAN)
            else:
                blink = math.sin(
                    pyxel.frame_count * 0.15 + d.blink_phase)
                pyxel.rect(sx - 1, sy - 1, 3, 3, d.color if blink > 0 else 2)

                # Show info when nearby
                px, py = self.proj.geo_to_screen(
                    self.player_lat, self.player_lon)
                dist = math.hypot(sx - px, sy - py)
                if dist < 35 and self.proj.zoom >= 4:
                    pyxel.text(
                        sx - len(d.name) * 2, sy - 7, d.name, d.color)
                    pyxel.text(sx - 10, sy + 5, f"{d.rssi}dBm", C_DIM)
                    if pyxel.frame_count % 4 < 2:
                        pyxel.line(px, py, sx, sy, C_HACK_CYAN)

    def _draw_scan_effects(self):
        """Draw scan pulse and scanning ring."""
        px, py = self.proj.geo_to_screen(self.player_lat, self.player_lon)

        # Ambient pulse
        if self.scan_pulse < 30:
            r = int(self.scan_pulse * 1.5)
            c = C_GRID if self.scan_pulse > 20 else C_HACK_CYAN
            pyxel.circb(px, py, r, c)

        # Active scan ring
        if self.ble_scanning or self.wifi_scanning:
            r = 25 + int(math.sin(pyxel.frame_count * 0.3) * 8)
            pyxel.circb(px, py, r, 12)

    def _draw_player(self):
        """Draw the Watch Dogs hacker character."""
        px, py = self.proj.geo_to_screen(self.player_lat, self.player_lon)

        # Shadow
        pyxel.elli(px - 3, py + 5, 7, 3, 1)

        # Body (hooded jacket)
        # Torso
        pyxel.rect(px - 3, py - 2, 7, 6, C_PLAYER_BODY)
        # Hood
        pyxel.rect(px - 2, py - 6, 5, 4, C_PLAYER_HOOD)
        pyxel.rect(px - 1, py - 7, 3, 1, C_PLAYER_HOOD)
        # Face (dark slit)
        pyxel.rect(px - 1, py - 4, 3, 2, 0)
        # Eyes (glowing)
        if pyxel.frame_count % 60 < 55:  # blink
            pyxel.pset(px - 1, py - 4, C_HACK_CYAN)
            pyxel.pset(px + 1, py - 4, C_HACK_CYAN)

        # Arms
        if self.hacking:
            # Arms forward (hacking pose)
            pyxel.rect(px - 5, py - 1, 2, 3, C_PLAYER_BODY)
            pyxel.rect(px + 4, py - 1, 2, 3, C_PLAYER_BODY)
            # Phone glow
            pyxel.rect(px - 6, py - 2, 2, 4, C_HACK_CYAN)
            if pyxel.frame_count % 4 < 2:
                pyxel.pset(px - 6, py - 3, C_SUCCESS)
        else:
            # Arms at sides
            pyxel.rect(px - 4, py - 1, 1, 4, C_PLAYER_BODY)
            pyxel.rect(px + 4, py - 1, 1, 4, C_PLAYER_BODY)

        # Legs (walking animation)
        walk_offset = 0
        if self.walk_frame > 0:
            walk_offset = 1 if self.walk_frame < 8 else -1

        pyxel.rect(px - 2, py + 4, 2, 3, C_PLAYER_HOOD)
        pyxel.rect(px + 1, py + 4, 2, 3, C_PLAYER_HOOD)
        # Feet
        pyxel.pset(px - 2 + walk_offset, py + 7, C_TEXT)
        pyxel.pset(px + 1 - walk_offset, py + 7, C_TEXT)

        # Direction indicator (subtle)
        dirs = {0: (0, 9), 1: (0, -9), 2: (-6, 0), 3: (6, 0)}
        ddx, ddy = dirs.get(self.player_dir, (0, 8))
        if pyxel.frame_count % 20 < 15:
            pyxel.pset(px + ddx, py + ddy, C_HACK_CYAN)

        # Trail particles (when moving)
        if self.walk_frame > 0 and pyxel.frame_count % 3 == 0:
            self.particles.append(
                Particle(px + random.randint(-2, 2),
                         py + 6, C_HACK_CYAN))

    def _draw_particles(self):
        for p in self.particles:
            alpha = min(1.0, p.life / 15)
            if alpha > 0.5:
                pyxel.pset(int(p.x), int(p.y), p.color)

    def _draw_hack_bar(self):
        """Draw hacking progress bar."""
        if not self.hacking or not self.hack_target:
            return
        sx, sy = self.proj.geo_to_screen(
            self.hack_target.lat, self.hack_target.lon)
        hy = sy - 14
        bw = 30
        fill = int(bw * self.hack_progress / 45)
        pyxel.rect(sx - 15, hy, bw, 4, 0)
        pyxel.rect(sx - 15, hy, fill, 4, C_SUCCESS)
        pyxel.rectb(sx - 15, hy, bw, 4, C_HACK_CYAN)
        pct = int(100 * self.hack_progress / 45)
        pyxel.text(sx - 18, hy - 8, f"HACKING {pct}%", C_HACK_CYAN)

    def _draw_glitch(self):
        if self.glitch_timer > 0:
            for _ in range(4):
                pyxel.rect(
                    random.randint(0, W - 1),
                    random.randint(0, H - 1),
                    random.randint(5, 50), 1,
                    random.choice([C_HACK_CYAN, C_TEXT, C_SUCCESS]),
                )

    def _draw_scanlines(self):
        for sl in self.scan_lines:
            pyxel.rect(0, sl, W, 1, C_GRID)

    def _draw_hud_top(self):
        """Top HUD bar."""
        pyxel.rect(0, 0, W, 12, C_HUD_BG)
        pyxel.line(0, 11, W - 1, 11, C_HUD_LINE)

        # Title
        pyxel.text(3, 3, "JanOS // WATCH_MODE", C_HACK_CYAN)

        # Level
        lvl_name = LEVEL_NAMES[min(self.level, len(LEVEL_NAMES) - 1)]
        pyxel.text(120, 3, f"LV:{self.level} {lvl_name}", C_SUCCESS)

        # XP bar
        xp_x = W - 80
        xp_w = 50
        xp_fill = int(xp_w * (self.xp % 200) / 200)
        pyxel.rect(xp_x, 3, xp_w, 6, C_GRID)
        pyxel.rect(xp_x, 3, xp_fill, 6, C_HACK_CYAN)
        pyxel.rectb(xp_x, 3, xp_w, 6, C_COAST)
        pyxel.text(xp_x + xp_w + 3, 3, f"XP:{self.xp}", C_DIM)

        # GPS indicator
        gps_c = C_SUCCESS if self.gps_fix else C_ERROR
        pyxel.text(W - 22, 3, "GPS", gps_c)

    def _draw_hud_bottom(self):
        """Bottom HUD bar."""
        pyxel.rect(0, H - 14, W, 14, C_HUD_BG)
        pyxel.line(0, H - 14, W - 1, H - 14, C_HUD_LINE)

        y = H - 11
        n_ble = len(self.ble_devices)
        n_wifi = len(self.wifi_networks)
        n_hs = sum(1 for m in self.markers if m.type == "handshake")
        n_pwn = sum(1 for d in self.ble_devices if d.hacked)
        n_pwn += sum(1 for n in self.wifi_networks if n.hacked)

        pyxel.text(3, y, f"BLE:{n_ble}", C_HACK_CYAN)
        pyxel.text(40, y, f"WiFi:{n_wifi}", C_WARNING)
        pyxel.text(80, y, f"HS:{n_hs}", C_ERROR)
        pyxel.text(110, y, f"PWN:{n_pwn}", C_SUCCESS)

        # Active tools
        tools = []
        if self.wifi_scanning:
            tools.append("WiFi")
        if self.ble_scanning:
            tools.append("BT")
        if self.sniffing:
            tools.append("SNF")
        if self.capturing_hs:
            tools.append("HS")
        if tools:
            dots = "." * ((pyxel.frame_count // 10) % 4)
            pyxel.text(145, y, " ".join(tools) + dots, 12)

        # Zoom info
        pyxel.text(W - 70, y, f"Z:{self.proj.label}", C_DIM)

        # Controls hint
        if not tools:
            pyxel.text(145, y, "[1-5]Tools [b]Scan", C_COAST)

        # Coordinates
        lat_c = "N" if self.player_lat >= 0 else "S"
        lon_c = "E" if self.player_lon >= 0 else "W"
        coord = f"{abs(self.player_lat):.3f}{lat_c} {abs(self.player_lon):.3f}{lon_c}"
        pyxel.text(W - 120, y, coord, C_DIM)

    def _draw_messages(self):
        """Draw message feed."""
        y = H - 26
        for text, timer, col in reversed(self.msgs):
            fade = min(timer, 30) / 30
            c = col if fade > 0.5 else C_COAST
            pyxel.text(4, y, text[:60], c)
            y -= 8
            if y < 20:
                break

    def _draw_radar(self):
        """Mini-radar in top-right corner."""
        rx, ry, rr = W - 22, 30, 16
        pyxel.rect(rx - rr - 1, ry - rr - 1, rr * 2 + 3, rr * 2 + 3, 0)
        pyxel.circb(rx, ry, rr, C_GRID)
        pyxel.line(rx, ry - rr, rx, ry + rr, C_GRID)
        pyxel.line(rx - rr, ry, rx + rr, ry, C_GRID)

        # Sweep
        sa = pyxel.frame_count * 0.04
        pyxel.line(rx, ry,
                   rx + int(math.cos(sa) * rr),
                   ry + int(math.sin(sa) * rr), C_HACK_CYAN)

        # Devices as dots
        scale = rr / max(self.proj.lon_span * 0.5, 0.001)
        for d in self.ble_devices:
            dx = (d.lon - self.player_lon) * scale
            dy = (self.player_lat - d.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                c = C_SUCCESS if d.hacked else d.color
                pyxel.pset(rx + int(dx), ry + int(dy), c)

        for n in self.wifi_networks:
            dx = (n.lon - self.player_lon) * scale
            dy = (self.player_lat - n.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                c = C_SUCCESS if n.hacked else C_WARNING
                pyxel.pset(rx + int(dx), ry + int(dy), c)

        # Markers
        for m in self.markers:
            dx = (m.lon - self.player_lon) * scale
            dy = (self.player_lat - m.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                pyxel.pset(rx + int(dx), ry + int(dy), C_ERROR)

        # Player dot (center)
        pyxel.pset(rx, ry, C_TEXT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    port = None
    loot = None
    args = sys.argv[1:]
    if args:
        port = args[0]
    if len(args) > 1:
        loot = args[1]
    WatchDogsGame(serial_port=port, loot_path=loot)


if __name__ == "__main__":
    main()
