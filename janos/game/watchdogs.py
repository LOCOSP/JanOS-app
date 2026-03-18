"""JanOS // WATCH_MODE — Open World Hacking RPG.

Overlay on JanOS — reads state via /tmp/janos_state.json,
sends commands via /tmp/janos_game_cmd.txt.
JanOS keeps running in the background controlling ESP32/GPS.

Controls:
  [TAB] Toggle cyberdeck menu    [SPACE] Hack nearby device
  [+/-] Zoom in/out              [ESC] Quit
  Arrow keys — manual pan (GPS overrides when fix available)
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
HUD_TOP = 12          # top bar height
HUD_BOT = 14          # bottom bar height
TERM_H = 80           # terminal panel height (~30%)
MAP_H = H - HUD_TOP - HUD_BOT - TERM_H  # map area height
TERM_Y = H - HUD_BOT - TERM_H  # terminal top y
STATE_FILE = "/tmp/janos_state.json"
CMD_FILE = "/tmp/janos_game_cmd.txt"

# Pyxel palette colors
C_WATER = 1
C_LAND = 3
C_COAST = 5
C_GRID = 1
C_COAT = 4            # brown trench coat
C_COAT_DARK = 5       # dark gray (coat shadow/lining)
C_CAP = 5             # dark gray cap
C_SCARF = 9           # orange/amber scarf accent
C_SKIN = 15           # peach skin
C_PANTS = 1           # dark blue jeans
C_BOOTS = 0           # black boots
C_HACK_CYAN = 3
C_HUD_BG = 0
C_HUD_LINE = 1
C_TEXT = 7
C_DIM = 13
C_WARNING = 10
C_ERROR = 8
C_SUCCESS = 11
C_DEVICE_SCREEN = 3  # phone/device screen glow
C_MENU_BG = 0
C_MENU_BORDER = 5
C_MENU_SEL = 3
C_MENU_TEXT = 7

# Zoom levels (more granular)
ZOOM_LEVELS = [
    (360.0, "WORLD"),
    (180.0, "HEMISPHERE"),
    (90.0, "CONTINENT"),
    (45.0, "REGION"),
    (20.0, "COUNTRY"),
    (10.0, "PROVINCE"),
    (5.0, "CITY"),
    (2.0, "DISTRICT"),
    (1.0, "QUARTER"),
    (0.5, "NEIGHBORHOOD"),
    (0.2, "STREET"),
    (0.1, "BLOCK"),
    (0.05, "BUILDING"),
    (0.02, "CLOSE-UP"),
]

LEVEL_NAMES = [
    "", "SCRIPT_KIDDIE", "SKIDDIE+", "HACKER",
    "NETRUNNER", "ELITE", "GHOST", "CYBER_GOD",
]

# Cyberdeck menu items
MENU_ITEMS = [
    ("1", "WiFi Scan",             "scan_networks",           "wardriving"),
    ("2", "BLE Scan",              "scan_bt",                 "bt_scanning"),
    ("3", "Packet Sniffer",        "start_sniffer",           "sniffer"),
    ("4", "Handshake Capture",     "start_handshake",         "handshake"),
    ("5", "Handshake No SD",       "start_handshake_serial",  "handshake"),
    ("b", "BLE Quick Scan",        "scan_bt",                 "bt_scanning"),
    ("x", "STOP ALL",              "stop",                    "_stop_all"),
]


# ---------------------------------------------------------------------------
# IPC
# ---------------------------------------------------------------------------

def read_janos_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def send_command(cmd: str) -> None:
    try:
        with open(CMD_FILE, "a") as f:
            f.write(cmd + "\n")
    except Exception:
        pass


def load_gps_markers(loot_path: str | None) -> list[dict]:
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
                    parts = label.rsplit("_", 1)
                    ssid = parts[0] if len(parts) > 1 else label
                    markers.append({"lat": float(lat), "lon": float(lon),
                                    "label": ssid[:20], "type": "handshake"})
            except Exception:
                continue
    return markers


# ---------------------------------------------------------------------------
# Game objects
# ---------------------------------------------------------------------------

class BleDevice:
    def __init__(self, lat, lon, mac, name, rssi):
        self.lat, self.lon, self.mac = lat, lon, mac
        self.name = name[:18]
        self.rssi, self.hacked = rssi, False
        self.blink_phase = random.random() * 6.28
        self.spawn_frame = 0

    @property
    def color(self):
        if self.rssi > -45: return 11
        if self.rssi > -60: return 10
        if self.rssi > -75: return 9
        return 8


class WifiNetwork:
    def __init__(self, lat, lon, bssid, ssid, channel, rssi):
        self.lat, self.lon, self.bssid = lat, lon, bssid
        self.ssid = ssid[:18]
        self.channel, self.rssi, self.hacked = channel, rssi, False
        self.spawn_frame = 0

    @property
    def color(self):
        if self.rssi > -50: return 11
        if self.rssi > -65: return 10
        if self.rssi > -80: return 9
        return 8


class Particle:
    def __init__(self, x, y, color=3):
        self.x, self.y = x, y
        a = random.random() * 6.28
        s = random.random() * 2.5 + 0.5
        self.vx, self.vy = math.cos(a) * s, math.sin(a) * s
        self.life = random.randint(10, 30)
        self.color = color


class MapMarker:
    def __init__(self, lat, lon, label, mtype):
        self.lat, self.lon, self.label, self.type = lat, lon, label, mtype


# ---------------------------------------------------------------------------
# Map projection
# ---------------------------------------------------------------------------

class MapProjection:
    def __init__(self):
        self.center_lat, self.center_lon = 20.0, 0.0
        self.zoom = 0
        self._target_lat, self._target_lon = 20.0, 0.0

    @property
    def lon_span(self): return ZOOM_LEVELS[self.zoom][0]
    @property
    def lat_span(self): return self.lon_span * MAP_H / W
    @property
    def label(self): return ZOOM_LEVELS[self.zoom][1]

    def smooth_move(self, lat, lon):
        self._target_lat, self._target_lon = lat, lon

    def update(self):
        self.center_lat += (self._target_lat - self.center_lat) * 0.08
        self.center_lon += (self._target_lon - self.center_lon) * 0.08

    def geo_to_screen(self, lat, lon):
        dx = lon - self.center_lon
        if dx > 180: dx -= 360
        elif dx < -180: dx += 360
        dy = self.center_lat - lat
        return (int(W/2 + dx * W / self.lon_span),
                int(HUD_TOP + MAP_H/2 + dy * MAP_H / self.lat_span))

    def screen_visible(self, sx, sy):
        return -20 <= sx <= W+20 and HUD_TOP-10 <= sy <= TERM_Y+10

    def zoom_in(self):
        if self.zoom < len(ZOOM_LEVELS) - 1: self.zoom += 1
    def zoom_out(self):
        if self.zoom > 0: self.zoom -= 1
    def reset_view(self):
        self.zoom = 0
        self._target_lat, self._target_lon = 20.0, 0.0


# ---------------------------------------------------------------------------
# Main game
# ---------------------------------------------------------------------------

class WatchDogsGame:

    def __init__(self, loot_path=None):
        pyxel.init(W, H, title="JanOS // WATCH_MODE", fps=FPS)
        pyxel.mouse(False)

        self.proj = MapProjection()
        self._coastlines = COASTLINES

        # Player — centered, GPS-driven
        self.player_lat, self.player_lon = 51.1, 17.9  # Opole default
        self.gps_fix = False
        self.gps_sats = 0
        self.gps_sats_vis = 0
        self._manual_move = False
        self._breath = 0  # idle animation

        # Game state
        self.xp, self.level = 0, 1
        self.ble_devices: list[BleDevice] = []
        self.wifi_networks: list[WifiNetwork] = []
        self.markers: list[MapMarker] = []
        self.particles: list[Particle] = []
        self.msgs: list[tuple[str, int, int]] = []
        self.scan_pulse = 0
        self.glitch_timer = 0
        self.scan_lines: list[int] = []
        self._known_ble: set[str] = set()
        self._known_wifi: set[str] = set()

        # Hack
        self.hack_target = None
        self.hack_progress = 0
        self.hacking = False

        # JanOS state
        self._esp32 = False
        self.wifi_scanning = False
        self.ble_scanning = False
        self.sniffing = False
        self.capturing_hs = False
        self._last_hs = 0

        # Menu
        self.menu_open = False
        self.menu_sel = 0
        self._pending_cmd = None
        self._pending_cmd_frame = 0
        self._pending_cmd_name = ""

        # Terminal output panel (bottom 30%) with scroll
        self.terminal_lines: list[str] = []
        self.term_scroll = 0     # 0 = bottom (auto-scroll), >0 = scrolled up
        self.term_line_h = 6     # pixel height per line (tight packing)

        # Loot GPS points (all types, from JanOS)
        self.loot_points: list[dict] = []  # {lat, lon, type, label}

        # Init
        try:
            open(CMD_FILE, "w").close()
        except Exception:
            pass

        self.proj.smooth_move(self.player_lat, self.player_lon)
        self.proj.zoom = 6  # city level

        self.msg("[SYS] JanOS // WATCH_MODE", C_HACK_CYAN)
        self.msg("[SYS] TAB=menu  SPACE=hack  +/-=zoom", C_DIM)

        pyxel.run(self.update, self.draw)

    def msg(self, text, color=C_TEXT):
        self.msgs.append((text, 180, color))
        if len(self.msgs) > 8:
            self.msgs.pop(0)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self):
        self._sync_janos()
        self._breath = (self._breath + 1) % 120

        # Camera always follows player
        self.proj.smooth_move(self.player_lat, self.player_lon)
        self.proj.update()

        self.scan_pulse = (self.scan_pulse + 1) % 60

        # Delayed command execution (after stop)
        if (self._pending_cmd and
                pyxel.frame_count >= self._pending_cmd_frame):
            send_command(self._pending_cmd)
            self.msg(f"[START] {self._pending_cmd_name}...", C_HACK_CYAN)
            self.glitch_timer = 5
            self._pending_cmd = None

        self._update_hack()

        # Particles
        for p in self.particles:
            p.x += p.vx; p.y += p.vy; p.life -= 1
        self.particles = [p for p in self.particles if p.life > 0]
        self.msgs = [(t, tm-1, c) for t, tm, c in self.msgs if tm > 1]

        if self.glitch_timer > 0:
            self.glitch_timer -= 1
        if pyxel.frame_count % 15 == 0:
            self.scan_lines = [random.randint(0, H-1) for _ in range(random.randint(0, 2))]

        # Keys
        if pyxel.btnp(pyxel.KEY_TAB):
            self.menu_open = not self.menu_open
            self.menu_sel = 0

        if self.menu_open:
            self._update_menu()
        else:
            # Zoom
            if (pyxel.btnp(pyxel.KEY_PLUS) or pyxel.btnp(pyxel.KEY_KP_PLUS)
                    or pyxel.btnp(pyxel.KEY_EQUALS)
                    or pyxel.btnp(pyxel.KEY_RIGHTBRACKET)):
                self.proj.zoom_in()
            if (pyxel.btnp(pyxel.KEY_MINUS) or pyxel.btnp(pyxel.KEY_KP_MINUS)
                    or pyxel.btnp(pyxel.KEY_LEFTBRACKET)):
                self.proj.zoom_out()
            if pyxel.btnp(pyxel.KEY_0):
                self.proj.reset_view()
            # Quick stop (S key)
            if pyxel.btnp(pyxel.KEY_S):
                send_command("stop")
                self.msg("[STOP] All operations stopped", C_WARNING)
                self.glitch_timer = 3
            # Manual pan (only without GPS fix)
            speed = self.proj.lon_span / W * 3
            if pyxel.btn(pyxel.KEY_UP):
                self.player_lat += speed; self._manual_move = True
            if pyxel.btn(pyxel.KEY_DOWN):
                self.player_lat -= speed; self._manual_move = True
            if pyxel.btn(pyxel.KEY_LEFT):
                self.player_lon -= speed; self._manual_move = True
            if pyxel.btn(pyxel.KEY_RIGHT):
                self.player_lon += speed; self._manual_move = True
            self.player_lat = max(-85, min(85, self.player_lat))

        # Terminal scroll (Page Up/Down)
        if pyxel.btnp(pyxel.KEY_PAGEUP):
            self.term_scroll = min(self.term_scroll + 5,
                                   max(0, len(self.terminal_lines) - 5))
        if pyxel.btnp(pyxel.KEY_PAGEDOWN):
            self.term_scroll = max(0, self.term_scroll - 5)
        # Auto-scroll to bottom when new lines arrive
        if pyxel.btnp(pyxel.KEY_END):
            self.term_scroll = 0

        if pyxel.btnp(pyxel.KEY_ESCAPE):
            self._cleanup()
            pyxel.quit()

    def _update_menu(self):
        if pyxel.btnp(pyxel.KEY_UP):
            self.menu_sel = (self.menu_sel - 1) % len(MENU_ITEMS)
        if pyxel.btnp(pyxel.KEY_DOWN):
            self.menu_sel = (self.menu_sel + 1) % len(MENU_ITEMS)
        if pyxel.btnp(pyxel.KEY_RETURN):
            self._activate_menu_item(self.menu_sel)
            self.menu_open = False

    def _activate_menu_item(self, idx):
        key, name, cmd, state_key = MENU_ITEMS[idx]
        if not self._esp32:
            self.msg(f"[ERR] No ESP32 — cannot start {name}", C_ERROR)
            return

        # Stop all — just send stop
        if state_key == "_stop_all":
            send_command("stop")
            self.msg("[STOP] All operations stopped", C_WARNING)
            self.glitch_timer = 3
            return

        # Check if this specific thing is already running
        running = False
        if state_key == "wardriving": running = self.wifi_scanning
        elif state_key == "bt_scanning": running = self.ble_scanning
        elif state_key == "sniffer": running = self.sniffing
        elif state_key == "handshake": running = self.capturing_hs

        if running:
            send_command("stop")
            self.msg(f"[STOP] {name}", C_DIM)
        else:
            # Queue: stop first, then new command after delay
            send_command("stop")
            self._pending_cmd = cmd
            self._pending_cmd_frame = pyxel.frame_count + 15  # ~0.5s delay
            self._pending_cmd_name = name
            self.msg(f"[INIT] {name}...", C_DIM)

    def _sync_janos(self):
        if pyxel.frame_count % 10 != 0:
            return
        state = read_janos_state()
        if not state:
            return

        # GPS
        lat = state.get("gps_lat", 0.0)
        lon = state.get("gps_lon", 0.0)
        fix = state.get("gps_fix", False)
        self.gps_sats = state.get("sats", 0)
        if fix and lat != 0 and lon != 0:
            self.player_lat, self.player_lon = lat, lon
            self.gps_fix = True
            self._manual_move = False
        else:
            self.gps_fix = fix

        # Status
        self._esp32 = state.get("esp32", False)
        self.wifi_scanning = state.get("wardriving", False)
        self.ble_scanning = state.get("bt_scanning", False)
        self.sniffing = state.get("sniffer", False)
        self.capturing_hs = state.get("handshake", False)

        # BLE
        for dev in state.get("ble_devices", []):
            mac = dev.get("mac", "")
            if mac and mac not in self._known_ble:
                self._known_ble.add(mac)
                d = BleDevice(
                    self.player_lat + (random.random()-0.5) * self.proj.lat_span * 0.3,
                    self.player_lon + (random.random()-0.5) * self.proj.lon_span * 0.3,
                    mac, dev.get("name", "?")[:18], dev.get("rssi", -70))
                d.spawn_frame = pyxel.frame_count
                self.ble_devices.append(d)
                self.msg(f"[BLE] {d.name} {mac[-8:]} {d.rssi}dBm", C_HACK_CYAN)
                self.xp += 10

        # WiFi
        for net in state.get("wifi_networks", []):
            bssid = net.get("bssid", "")
            if bssid and bssid not in self._known_wifi:
                self._known_wifi.add(bssid)
                n = WifiNetwork(
                    self.player_lat + (random.random()-0.5) * self.proj.lat_span * 0.2,
                    self.player_lon + (random.random()-0.5) * self.proj.lon_span * 0.2,
                    bssid, net.get("ssid", "?")[:18],
                    net.get("channel", 0), net.get("rssi", -70))
                n.spawn_frame = pyxel.frame_count
                self.wifi_networks.append(n)
                self.msg(f"[WiFi] {n.ssid} Ch:{n.channel}", C_WARNING)
                self.xp += 15

        # Handshakes
        hs = state.get("handshakes", 0)
        if hs > self._last_hs and self._last_hs > 0:
            for _ in range(hs - self._last_hs):
                self.msg("[HS] Handshake captured!", C_SUCCESS)
                self.xp += 200
                self.glitch_timer = 10
                self.markers.append(MapMarker(
                    self.player_lat, self.player_lon, "HS", "handshake"))
                px, py = self.proj.geo_to_screen(self.player_lat, self.player_lon)
                for _ in range(30):
                    self.particles.append(Particle(px, py, random.choice([11, 10, 3])))
        self._last_hs = hs

        # Serial output → terminal panel (smart filter)
        new_lines = state.get("serial_lines", [])
        for line in new_lines:
            s = line.strip()
            # Always skip: empty, prompt, OK
            if not s or s == ">" or s == "OK":
                continue
            # Skip echo'd commands (> cmd)
            if s.startswith("> ") and len(s) < 40:
                continue
            # Skip memory/system noise
            if s.startswith("[MEM]"):
                continue
            if "already running" in s:
                continue
            skip_prefixes = (
                "Command returned", "Stop command received",
                "Stopping ", "cleanup",
            )
            if any(s.startswith(p) for p in skip_prefixes):
                continue

            # During HS capture: allow PCAP/base64 (proof of capture)
            # Otherwise: filter out binary/system data
            if not self.capturing_hs:
                # Skip base64 blobs
                if len(s) > 50 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n" for c in s):
                    continue
                # Skip PCAP/HCCAPX markers
                if s.startswith("---") and ("PCAP" in s or "HCCAPX" in s):
                    continue
                if s.startswith("PCAP buffer:") or s.startswith("Dumping"):
                    continue
                if s.startswith("=====") or s.startswith("Total handshakes"):
                    continue
            # Skip raw hex dumps (sniffer packet data)
            if len(s) > 30 and s.count(":") > 5 and not any(
                    kw in s for kw in ["RSSI", "SSID", "Name", "AP", "BSSID"]):
                continue

            self.terminal_lines.append(s)
        if len(self.terminal_lines) > 500:
            self.terminal_lines = self.terminal_lines[-500:]

        # Loot GPS points (all types — wifi, bt, handshake, meshcore)
        loot = state.get("loot_points", [])
        if loot:
            self.loot_points = loot

        # Level up
        new_lvl = 1 + self.xp // 200
        if new_lvl > self.level:
            self.level = new_lvl
            self.msg(f"[LEVEL UP] {LEVEL_NAMES[min(self.level, len(LEVEL_NAMES)-1)]}", C_WARNING)

    def _update_hack(self):
        if pyxel.btn(pyxel.KEY_SPACE) and not self.menu_open:
            if not self.hacking:
                best, best_d = None, 999
                px, py = W // 2, HUD_TOP + MAP_H // 2  # player always center
                for d in self.ble_devices + self.wifi_networks:
                    if d.hacked: continue
                    sx, sy = self.proj.geo_to_screen(d.lat, d.lon)
                    dist = math.hypot(sx - px, sy - py)
                    if dist < 40 and dist < best_d:
                        best, best_d = d, dist
                if best:
                    self.hacking, self.hack_target, self.hack_progress = True, best, 0
            elif self.hack_target:
                self.hack_progress += 1
                if pyxel.frame_count % 3 == 0: self.glitch_timer = 2
                if self.hack_progress >= 45:
                    self.hack_target.hacked = True
                    self.hacking = False
                    self.xp += 50
                    name = getattr(self.hack_target, "name", getattr(self.hack_target, "ssid", "?"))
                    self.msg(f"[PWNED] {name}", C_SUCCESS)
                    sx, sy = self.proj.geo_to_screen(self.hack_target.lat, self.hack_target.lon)
                    for _ in range(20):
                        self.particles.append(Particle(sx, sy, C_SUCCESS))
                    self.hack_target = None
        else:
            if self.hacking and self.hack_progress < 45:
                self.hacking, self.hack_target = False, None

    def _cleanup(self):
        """Clean exit — JanOS keeps running, ESP32 keeps doing its thing.
        User can stop via [x] STOP ALL in menu, or from JanOS TUI."""
        pass

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self):
        pyxel.cls(C_WATER)
        self._draw_coastlines()
        self._draw_grid()
        self._draw_loot_points()
        self._draw_markers()
        self._draw_wifi()
        self._draw_ble()
        self._draw_scan_fx()
        self._draw_player()
        self._draw_particles()
        self._draw_hack_bar()
        self._draw_glitch()
        self._draw_terminal()
        self._draw_hud_top()
        self._draw_hud_bottom()
        self._draw_messages()
        self._draw_radar()
        self._draw_scanlines()
        if self.menu_open:
            self._draw_menu()

    # -- Coastlines --
    def _draw_coastlines(self):
        vl = self.proj.center_lat - self.proj.lat_span
        vh = self.proj.center_lat + self.proj.lat_span
        wl = self.proj.center_lon - self.proj.lon_span
        wh = self.proj.center_lon + self.proj.lon_span
        for seg in self._coastlines:
            if len(seg) < 2: continue
            lats = [p[0] for p in seg]
            lons = [p[1] for p in seg]
            if max(lats) < vl or min(lats) > vh: continue
            if (max(lons) - min(lons)) < 180:
                if max(lons) < wl or min(lons) > wh: continue
            psx, psy = None, None
            for lat, lon in seg:
                sx, sy = self.proj.geo_to_screen(lat, lon)
                if psx is not None and abs(sx - psx) < W * 0.8:
                    pyxel.line(psx, psy, sx, sy, C_LAND)
                psx, psy = sx, sy
            if self.proj.zoom >= 5:
                for lat, lon in seg:
                    sx, sy = self.proj.geo_to_screen(lat, lon)
                    if self.proj.screen_visible(sx, sy):
                        pyxel.pset(sx, sy, C_COAST)

    # -- Grid --
    def _draw_grid(self):
        if self.proj.zoom < 4: return
        sp = max(0.01, self.proj.lon_span / 8)
        for e in [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30]:
            if e >= sp: sp = e; break
        lat = int((self.proj.center_lat - self.proj.lat_span) / sp) * sp
        while lat < self.proj.center_lat + self.proj.lat_span:
            _, sy = self.proj.geo_to_screen(lat, 0)
            if HUD_TOP < sy < TERM_Y:
                pyxel.line(0, sy, W-1, sy, C_GRID)
            lat += sp
        lon = int((self.proj.center_lon - self.proj.lon_span) / sp) * sp
        while lon < self.proj.center_lon + self.proj.lon_span:
            sx, _ = self.proj.geo_to_screen(0, lon)
            if 0 < sx < W:
                pyxel.line(sx, HUD_TOP, sx, TERM_Y, C_GRID)
            lon += sp

    # -- Loot GPS points (wifi=green, bt=cyan, handshake=red, meshcore=yellow) --
    def _draw_loot_points(self):
        # Color map by type
        TYPE_COLORS = {
            "wifi": C_SUCCESS,      # green
            "bt": C_HACK_CYAN,      # cyan
            "handshake": C_ERROR,   # red
            "meshcore": C_WARNING,  # yellow
        }
        for pt in self.loot_points:
            sx, sy = self.proj.geo_to_screen(pt["lat"], pt["lon"])
            if not self.proj.screen_visible(sx, sy):
                continue
            c = TYPE_COLORS.get(pt.get("type", ""), C_DIM)
            # Size based on zoom
            if self.proj.zoom >= 8:
                pyxel.circ(sx, sy, 2, c)
                # Label at high zoom
                if self.proj.zoom >= 10:
                    label = pt.get("label", "")[:16]
                    pyxel.text(sx + 4, sy - 2, label, c)
            elif self.proj.zoom >= 5:
                pyxel.rect(sx, sy, 2, 2, c)
            else:
                pyxel.pset(sx, sy, c)

    # -- Markers (handshake locks — separate from loot dots) --
    def _draw_markers(self):
        for m in self.markers:
            sx, sy = self.proj.geo_to_screen(m.lat, m.lon)
            if not self.proj.screen_visible(sx, sy): continue
            if pyxel.frame_count % 30 < 20:
                pyxel.circb(sx, sy, 4, C_ERROR)
            pyxel.rect(sx-2, sy-1, 5, 4, C_ERROR)
            pyxel.rect(sx-1, sy-3, 3, 2, C_ERROR)
            pyxel.pset(sx, sy, C_WARNING)
            if self.proj.zoom >= 5:
                pyxel.text(sx+5, sy-3, m.label, C_ERROR)

    # -- WiFi --
    def _draw_wifi(self):
        for net in self.wifi_networks:
            sx, sy = self.proj.geo_to_screen(net.lat, net.lon)
            if not self.proj.screen_visible(sx, sy): continue
            age = pyxel.frame_count - net.spawn_frame
            if age < 15:
                if age % 2 == 0: pyxel.circb(sx, sy, 15-age, C_WARNING)
                continue
            if net.hacked:
                pyxel.line(sx-2, sy, sx, sy-3, C_SUCCESS)
                pyxel.line(sx+2, sy, sx, sy-3, C_SUCCESS)
                pyxel.line(sx-2, sy, sx, sy+2, C_SUCCESS)
                pyxel.line(sx+2, sy, sx, sy+2, C_SUCCESS)
            else:
                blink = math.sin(pyxel.frame_count * 0.1 + hash(net.bssid) % 100)
                c = net.color if blink > 0 else 2
                pyxel.pset(sx, sy, c)
                if self.proj.zoom >= 5: pyxel.circb(sx, sy, 3, c)

    # -- BLE --
    def _draw_ble(self):
        for d in self.ble_devices:
            sx, sy = self.proj.geo_to_screen(d.lat, d.lon)
            if not self.proj.screen_visible(sx, sy): continue
            age = pyxel.frame_count - d.spawn_frame
            if age < 15:
                if age % 2 == 0: pyxel.circb(sx, sy, 15-age, C_HACK_CYAN)
                continue
            if d.hacked:
                pyxel.rect(sx-2, sy-2, 5, 5, C_HACK_CYAN)
                pyxel.pset(sx, sy, C_SUCCESS)
            else:
                blink = math.sin(pyxel.frame_count * 0.15 + d.blink_phase)
                pyxel.rect(sx-1, sy-1, 3, 3, d.color if blink > 0 else 2)

    # -- Scan effects --
    def _draw_scan_fx(self):
        cx, cy = W // 2, HUD_TOP + MAP_H // 2
        if self.scan_pulse < 30:
            r = int(self.scan_pulse * 1.5)
            pyxel.circb(cx, cy, r, C_GRID if self.scan_pulse > 20 else C_HACK_CYAN)
        if self.ble_scanning or self.wifi_scanning:
            r = 25 + int(math.sin(pyxel.frame_count * 0.3) * 8)
            pyxel.circb(cx, cy, r, 12)

    # -- Player (small, centered — Aiden Pearce colors) --
    def _draw_player(self):
        if self.menu_open:
            return  # big version drawn in menu
        cx, cy = W // 2, HUD_TOP + MAP_H // 2
        b = math.sin(self._breath * 0.05) * 0.5

        # Shadow
        pyxel.elli(cx - 4, cy + 10, 9, 3, 1)
        # Boots
        pyxel.rect(cx - 3, cy + 8, 2, 2, C_BOOTS)
        pyxel.rect(cx + 1, cy + 8, 2, 2, C_BOOTS)
        # Legs (dark jeans)
        pyxel.rect(cx - 3, cy + 4, 2, 5, C_PANTS)
        pyxel.rect(cx + 1, cy + 4, 2, 5, C_PANTS)
        # Coat
        pyxel.rect(cx - 4, cy - 3, 9, 8, C_COAT)
        pyxel.rect(cx - 1, cy - 2, 3, 6, C_COAT_DARK)  # inner
        # Collar
        pyxel.rect(cx - 4, cy - 4, 2, 2, C_COAT)
        pyxel.rect(cx + 3, cy - 4, 2, 2, C_COAT)
        # Head
        pyxel.rect(cx - 2, cy - 8, 5, 5, C_SKIN)
        # Cap
        pyxel.rect(cx - 3, cy - 10, 7, 3, C_CAP)
        pyxel.rect(cx - 4, cy - 8, 9, 1, C_CAP)
        # Eyes
        if pyxel.frame_count % 90 < 85:
            pyxel.pset(cx - 1, cy - 6, 0)
            pyxel.pset(cx + 1, cy - 6, 0)
        # Scarf (orange accent)
        pyxel.rect(cx - 2, cy - 5, 5, 2, C_SCARF)
        # Phone in right hand
        ay = cy - 1 + int(b)
        pyxel.rect(cx + 5, cy - 2, 2, 5, C_COAT)  # arm
        pyxel.rect(cx + 7, ay, 3, 4, 0)             # phone
        pyxel.rect(cx + 8, ay + 1, 1, 2, C_DEVICE_SCREEN)
        # Left arm
        pyxel.rect(cx - 6, cy - 2, 2, 5, C_COAT)

    def _draw_particles(self):
        for p in self.particles:
            if p.life > 7:
                pyxel.pset(int(p.x), int(p.y), p.color)

    def _draw_hack_bar(self):
        if not self.hacking or not self.hack_target: return
        sx, sy = self.proj.geo_to_screen(self.hack_target.lat, self.hack_target.lon)
        hy = sy - 14
        fill = int(30 * self.hack_progress / 45)
        pyxel.rect(sx-15, hy, 30, 4, 0)
        pyxel.rect(sx-15, hy, fill, 4, C_SUCCESS)
        pyxel.rectb(sx-15, hy, 30, 4, C_HACK_CYAN)
        pyxel.text(sx-18, hy-8, f"HACKING {int(100*self.hack_progress/45)}%", C_HACK_CYAN)

    def _draw_glitch(self):
        if self.glitch_timer > 0:
            for _ in range(4):
                pyxel.rect(random.randint(0, W-1), random.randint(0, H-1),
                           random.randint(5, 50), 1, random.choice([C_HACK_CYAN, C_TEXT, C_SUCCESS]))

    def _draw_scanlines(self):
        for sl in self.scan_lines:
            pyxel.rect(0, sl, W, 1, C_GRID)

    # -- Terminal panel (bottom 30%) with scroll --
    def _draw_terminal(self):
        # Background
        pyxel.rect(0, TERM_Y, W, TERM_H, 0)
        # Top border
        pyxel.line(0, TERM_Y, W - 1, TERM_Y, C_MENU_BORDER)
        # Header
        pyxel.text(3, TERM_Y + 1, "> OUTPUT", C_HACK_CYAN)
        # Active tool
        tool_name = ""
        if self.capturing_hs: tool_name = "HANDSHAKE"
        elif self.wifi_scanning: tool_name = "WiFi SCAN"
        elif self.ble_scanning: tool_name = "BT SCAN"
        elif self.sniffing: tool_name = "SNIFFER"
        if tool_name:
            pyxel.text(54, TERM_Y + 1, tool_name, C_WARNING)
        # Scroll indicator
        if self.term_scroll > 0:
            pyxel.text(W - 60, TERM_Y + 1, f"SCROLL +{self.term_scroll}", C_DIM)
        pyxel.text(W - 30, TERM_Y + 1, f"L:{len(self.terminal_lines)}", C_DIM)

        # Tight packing: 5px per line
        line_h = 5
        content_y = TERM_Y + 8
        content_h = TERM_H - 10
        max_visible = content_h // line_h

        # Visible slice with scroll
        total = len(self.terminal_lines)
        if self.term_scroll == 0:
            start = max(0, total - max_visible)
            end = total
        else:
            end = max(0, total - self.term_scroll)
            start = max(0, end - max_visible)

        y = content_y
        for i in range(start, end):
            line = self.terminal_lines[i]
            # Fit more text per line
            display = line[:110]
            # Color by content type
            c = C_TEXT
            if line.startswith(">>>"):
                c = C_HACK_CYAN
            elif "RSSI:" in line or "dBm" in line:
                c = C_HACK_CYAN
            elif "SSID:" in line or "SSID" in line:
                c = C_SUCCESS
            elif "AP:" in line or "BSSID:" in line:
                c = C_SUCCESS
            elif "handshake" in line.lower() or "captured" in line.lower():
                c = C_WARNING
            elif "scan" in line.lower() and "start" in line.lower():
                c = C_HACK_CYAN
            elif any(x in line for x in ["Ch:", "Channel", "Auth:"]):
                c = 6  # light gray — detail info
            pyxel.text(4, y, display, c)
            y += line_h
            if y >= TERM_Y + TERM_H - 2:
                break

        # Cursor blink at bottom
        if self.term_scroll == 0 and pyxel.frame_count % 30 < 20:
            pyxel.text(4, min(y, TERM_Y + TERM_H - 7), "_", C_HACK_CYAN)

        # Scroll hint
        if total > max_visible and self.term_scroll == 0:
            pyxel.text(W - 80, TERM_Y + TERM_H - 7, "PgUp/PgDn scroll", C_DIM)

    # -- HUD top --
    def _draw_hud_top(self):
        pyxel.rect(0, 0, W, 12, C_HUD_BG)
        pyxel.line(0, 11, W-1, 11, C_HUD_LINE)
        pyxel.text(3, 3, "JanOS // WATCH_MODE", C_HACK_CYAN)
        lvl = LEVEL_NAMES[min(self.level, len(LEVEL_NAMES)-1)]
        pyxel.text(130, 3, f"LV:{self.level} {lvl}", C_SUCCESS)
        # XP bar
        xw = 50
        xf = int(xw * (self.xp % 200) / 200)
        pyxel.rect(W-82, 3, xw, 6, C_GRID)
        pyxel.rect(W-82, 3, xf, 6, C_HACK_CYAN)
        pyxel.rectb(W-82, 3, xw, 6, C_COAST)
        pyxel.text(W-28, 3, f"{self.xp}", C_DIM)

    # -- HUD bottom --
    def _draw_hud_bottom(self):
        pyxel.rect(0, H-14, W, 14, C_HUD_BG)
        pyxel.line(0, H-14, W-1, H-14, C_HUD_LINE)
        y = H - 11

        # Left: stats
        pyxel.text(3, y, f"BLE:{len(self.ble_devices)}", C_HACK_CYAN)
        pyxel.text(38, y, f"WiFi:{len(self.wifi_networks)}", C_WARNING)
        n_hs = sum(1 for m in self.markers if m.type == "handshake")
        pyxel.text(80, y, f"HS:{n_hs}", C_ERROR)
        n_pwn = sum(1 for d in self.ble_devices if d.hacked) + sum(1 for n in self.wifi_networks if n.hacked)
        pyxel.text(110, y, f"PWN:{n_pwn}", C_SUCCESS)

        # Active tools
        tools = []
        if self.wifi_scanning: tools.append("WiFi")
        if self.ble_scanning: tools.append("BT")
        if self.sniffing: tools.append("SNF")
        if self.capturing_hs: tools.append("HS")
        if tools:
            dots = "." * ((pyxel.frame_count // 10) % 4)
            pyxel.text(145, y, " ".join(tools) + dots, 12)
        elif not self.menu_open:
            pyxel.text(145, y, "[TAB]Menu [S]Stop", C_COAST)

        # Right: GPS status (real JanOS state)
        if self.gps_fix:
            lat_c = "N" if self.player_lat >= 0 else "S"
            lon_c = "E" if self.player_lon >= 0 else "W"
            gps_txt = f"{abs(self.player_lat):.4f}{lat_c} {abs(self.player_lon):.4f}{lon_c}"
            pyxel.text(W - 130, y, gps_txt, C_SUCCESS)
        else:
            if self.gps_sats > 0:
                pyxel.text(W - 100, y, f"Acquiring fix... Vis:{self.gps_sats}", C_WARNING)
            else:
                pyxel.text(W - 90, y, "Waiting for GPS fix", C_ERROR)

        # Zoom
        pyxel.text(W - 55, 3, f"Z:{self.proj.label}", C_DIM)

    # -- Messages (above terminal) --
    def _draw_messages(self):
        y = TERM_Y - 10
        for text, timer, col in reversed(self.msgs):
            c = col if min(timer, 30) / 30 > 0.5 else C_COAST
            pyxel.text(4, y, text[:60], c)
            y -= 8
            if y < HUD_TOP + 10: break

    # -- Radar --
    def _draw_radar(self):
        rx, ry, rr = W-22, 30, 16
        pyxel.rect(rx-rr-1, ry-rr-1, rr*2+3, rr*2+3, 0)
        pyxel.circb(rx, ry, rr, C_GRID)
        pyxel.line(rx, ry-rr, rx, ry+rr, C_GRID)
        pyxel.line(rx-rr, ry, rx+rr, ry, C_GRID)
        sa = pyxel.frame_count * 0.04
        pyxel.line(rx, ry, rx+int(math.cos(sa)*rr), ry+int(math.sin(sa)*rr), C_HACK_CYAN)
        scale = rr / max(self.proj.lon_span * 0.5, 0.001)
        for d in self.ble_devices:
            dx, dy = (d.lon - self.player_lon) * scale, (self.player_lat - d.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                pyxel.pset(rx+int(dx), ry+int(dy), C_SUCCESS if d.hacked else d.color)
        for n in self.wifi_networks:
            dx, dy = (n.lon - self.player_lon) * scale, (self.player_lat - n.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                pyxel.pset(rx+int(dx), ry+int(dy), C_SUCCESS if n.hacked else C_WARNING)
        for m in self.markers:
            dx, dy = (m.lon - self.player_lon) * scale, (self.player_lat - m.lat) * scale
            if abs(dx) < rr and abs(dy) < rr:
                pyxel.pset(rx+int(dx), ry+int(dy), C_ERROR)
        # Loot points on radar (sampled to avoid perf issues)
        loot_colors = {"wifi": C_SUCCESS, "bt": C_HACK_CYAN,
                        "handshake": C_ERROR, "meshcore": C_WARNING}
        step = max(1, len(self.loot_points) // 100)
        for i in range(0, len(self.loot_points), step):
            pt = self.loot_points[i]
            dx = (pt["lon"] - self.player_lon) * scale
            dy = (self.player_lat - pt["lat"]) * scale
            if abs(dx) < rr and abs(dy) < rr:
                pyxel.pset(rx+int(dx), ry+int(dy),
                           loot_colors.get(pt.get("type", ""), C_DIM))
        pyxel.pset(rx, ry, C_TEXT)

    # -- Menu: big Aiden with options scattered around (Watch Dogs style) --
    def _draw_menu(self):
        # Dark overlay
        for y in range(HUD_TOP, TERM_Y):
            if y % 2 == 0:
                pyxel.line(0, y, W - 1, y, 0)

        cx = W // 2
        # Character positioned slightly left, waist-up (cut at belt)
        px = cx - 20
        belt_y = TERM_Y - 4  # bottom of character = bottom of map area
        # Scale: ~80px from belt to top of cap

        # === Coat torso (brown trench, wide) ===
        pyxel.rect(px - 14, belt_y - 35, 29, 36, C_COAT)
        # Coat darker side panels
        pyxel.rect(px - 14, belt_y - 35, 5, 36, C_COAT_DARK)
        pyxel.rect(px + 10, belt_y - 35, 5, 36, C_COAT_DARK)
        # Center seam / inner layer
        pyxel.rect(px - 3, belt_y - 30, 7, 28, C_COAT_DARK)
        # Lapel / collar folds
        pyxel.line(px - 3, belt_y - 30, px - 8, belt_y - 42, C_COAT_DARK)
        pyxel.line(px + 3, belt_y - 30, px + 8, belt_y - 42, C_COAT_DARK)
        # Belt
        pyxel.rect(px - 13, belt_y - 2, 27, 3, 0)
        pyxel.rect(px - 1, belt_y - 2, 3, 3, 6)  # buckle

        # === Collar turned up (high, like Aiden) ===
        pyxel.rect(px - 14, belt_y - 42, 6, 10, C_COAT)
        pyxel.rect(px + 9, belt_y - 42, 6, 10, C_COAT)
        # Collar inner
        pyxel.rect(px - 10, belt_y - 40, 3, 6, C_COAT_DARK)
        pyxel.rect(px + 8, belt_y - 40, 3, 6, C_COAT_DARK)

        # === Neck / Scarf (orange accent, wrapped) ===
        pyxel.rect(px - 6, belt_y - 44, 13, 5, C_SCARF)
        pyxel.rect(px - 5, belt_y - 42, 11, 2, 9)  # brighter wrap
        # Scarf texture lines
        pyxel.line(px - 4, belt_y - 43, px + 5, belt_y - 43, C_COAT_DARK)

        # === Head (larger, detailed) ===
        head_y = belt_y - 60
        # Face shape
        pyxel.rect(px - 7, head_y, 15, 14, C_SKIN)
        # Jawline (slightly narrower bottom)
        pyxel.rect(px - 6, head_y + 11, 13, 3, C_SKIN)
        # Cap (flat cap, dark, with prominent brim)
        pyxel.rect(px - 8, head_y - 6, 17, 7, C_CAP)
        pyxel.rect(px - 10, head_y, 21, 3, C_CAP)  # wide brim
        # Cap shadow on forehead
        pyxel.line(px - 7, head_y + 2, px + 7, head_y + 2, C_COAT_DARK)
        # Eyes (narrow, intense)
        if pyxel.frame_count % 120 < 115:
            pyxel.rect(px - 4, head_y + 5, 3, 2, 0)  # left eye
            pyxel.rect(px + 2, head_y + 5, 3, 2, 0)  # right eye
            # Eye highlights
            pyxel.pset(px - 3, head_y + 5, 7)
            pyxel.pset(px + 3, head_y + 5, 7)
        # Nose shadow
        pyxel.pset(px, head_y + 8, C_COAT_DARK)
        # Face mask / scarf pulled up (covers lower face)
        pyxel.rect(px - 6, head_y + 9, 13, 5, C_SCARF)
        pyxel.rect(px - 5, head_y + 10, 11, 3, 9)  # scarf folds

        # === Left arm (at side, relaxed) ===
        pyxel.rect(px - 19, belt_y - 33, 6, 20, C_COAT)
        pyxel.rect(px - 18, belt_y - 14, 4, 4, C_SKIN)  # hand

        # === Right arm (holding phone up) ===
        # Upper arm
        pyxel.rect(px + 15, belt_y - 35, 6, 12, C_COAT)
        # Forearm (angled up toward phone)
        pyxel.rect(px + 17, belt_y - 40, 5, 10, C_COAT)
        # Hand holding phone
        pyxel.rect(px + 19, belt_y - 44, 4, 5, C_SKIN)

        # === Phone / device (the hub — all lines converge here) ===
        phone_x = px + 20
        phone_y = belt_y - 52
        # Phone body
        pyxel.rect(phone_x, phone_y, 8, 12, 0)
        pyxel.rectb(phone_x, phone_y, 8, 12, 5)
        # Screen (glowing)
        pyxel.rect(phone_x + 1, phone_y + 1, 6, 10, C_DEVICE_SCREEN)
        # Screen content flicker
        if pyxel.frame_count % 8 < 6:
            pyxel.pset(phone_x + 2, phone_y + 2, C_SUCCESS)
            pyxel.pset(phone_x + 5, phone_y + 4, C_HACK_CYAN)
            pyxel.pset(phone_x + 3, phone_y + 7, C_WARNING)
            pyxel.pset(phone_x + 4, phone_y + 9, C_TEXT)
        # Phone glow aura
        for gx in range(-2, 10):
            for gy in range(-2, 14):
                if random.random() < 0.02:
                    pyxel.pset(phone_x + gx, phone_y + gy, C_HACK_CYAN)

        # Phone center point (where lines converge)
        phone_cx = phone_x + 4
        phone_cy = phone_y + 6

        # === Options — lines from PHONE to scattered labels ===
        option_positions = [
            (-180, -55),    # far top-left
            (-170, -25),    # mid-left upper
            (-160, 5),      # mid-left
            (60, -55),      # top-right
            (70, -25),      # mid-right upper
            (60, 5),        # mid-right
            (50, 30),       # bottom-right (STOP)
        ]

        for i, (key, name, cmd, state_key) in enumerate(MENU_ITEMS):
            if i >= len(option_positions):
                break
            ox, oy = option_positions[i]
            tx = phone_cx + ox
            ty = phone_cy + oy

            running = False
            if state_key == "wardriving": running = self.wifi_scanning
            elif state_key == "bt_scanning": running = self.ble_scanning
            elif state_key == "sniffer": running = self.sniffing
            elif state_key == "handshake": running = self.capturing_hs
            is_stop = (state_key == "_stop_all")

            sel = (i == self.menu_sel)

            # Line from phone to option label
            lc = C_HACK_CYAN if sel else (C_ERROR if is_stop else C_COAST)
            # Line with a small break/node near the phone
            node_x = phone_cx + (6 if ox > 0 else -6)
            node_y = phone_cy + oy // 4
            pyxel.line(phone_cx, phone_cy, node_x, node_y, lc)
            pyxel.line(node_x, node_y, tx + 40, ty + 4, lc)
            # Node dot
            pyxel.circ(node_x, node_y, 1, lc)

            # Option box
            bw = 85
            if sel:
                pyxel.rect(tx, ty - 1, bw, 11, C_HACK_CYAN if not is_stop else C_ERROR)
                pyxel.rectb(tx, ty - 1, bw, 11, C_TEXT)
                label_c = 0
            else:
                pyxel.rect(tx, ty - 1, bw, 11, 0)
                pyxel.rectb(tx, ty - 1, bw, 11, C_ERROR if is_stop else C_COAST)
                label_c = C_ERROR if is_stop else C_TEXT

            pyxel.text(tx + 2, ty + 1, f"[{key}] {name}", label_c)
            if running:
                pyxel.text(tx + bw - 14, ty + 1, "ON",
                           C_SUCCESS if not sel else 0)

            # Corner brackets (Watch Dogs UI style)
            # Top-left corner
            pyxel.line(tx - 1, ty - 2, tx + 3, ty - 2, C_TEXT)
            pyxel.line(tx - 1, ty - 2, tx - 1, ty + 2, C_TEXT)
            # Bottom-right corner
            pyxel.line(tx + bw - 3, ty + 10, tx + bw, ty + 10, C_TEXT)
            pyxel.line(tx + bw, ty + 7, tx + bw, ty + 10, C_TEXT)

        # ESP32 status
        esp_c = C_SUCCESS if self._esp32 else C_ERROR
        esp_t = "// ESP32 CONNECTED" if self._esp32 else "// ESP32 OFFLINE"
        pyxel.text(10, HUD_TOP + 5, esp_t, esp_c)

        # Hints
        pyxel.text(W // 2 - 55, TERM_Y - 8, "UP/DOWN  ENTER  TAB", C_DIM)

        # Title
        pyxel.text(10, HUD_TOP + 15, "// CONNECTION IS POWER", C_HACK_CYAN)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    loot = None
    args = sys.argv[1:]
    # args[0] = serial port (unused, JanOS handles it)
    if len(args) > 1:
        loot = args[1]
    WatchDogsGame(loot_path=loot)


if __name__ == "__main__":
    main()
