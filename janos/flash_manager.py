"""Download firmware from GitHub and flash ESP32-C5 via esptool."""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import re
import zipfile
from queue import Queue
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

from .config import (
    FLASH_CHIP, FLASH_MODE, FLASH_FREQ,
    FLASH_BOARDS, FIRMWARE_RELEASE_URL, FIRMWARE_DIR,
)

log = logging.getLogger(__name__)


class FlashManager:
    """Download firmware and flash ESP32-C5 with live progress via queue."""

    def __init__(self) -> None:
        self.queue: Queue = Queue()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.done = False
        self.success = False
        self._release_tag: str = ""
        self._board: str = "wroom"

    def _emit(self, line: str, attr: str = "default") -> None:
        self.queue.put((line, attr))

    def start(self, port: str, erase: bool = False,
              board: str = "wroom") -> None:
        if self._thread and self._thread.is_alive():
            return
        self.done = False
        self.success = False
        self.running = True
        self._board = board
        self._thread = threading.Thread(
            target=self._run, args=(port, erase), daemon=True,
        )
        self._thread.start()

    @property
    def _profile(self) -> dict:
        return FLASH_BOARDS.get(self._board, FLASH_BOARDS["wroom"])

    # ------------------------------------------------------------------
    # Main flash pipeline (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self, port: str, erase: bool) -> None:
        try:
            profile = self._profile
            self._emit(f"Board: {profile['label']}", "dim")
            self._emit(f"Target port: {port}", "dim")

            if self._board == "xiao":
                self._emit("", "default")
                self._emit(
                    "XIAO: Hold BOOT + press RESET, then release BOOT.",
                    "warning",
                )
                self._emit(
                    "Device must be in bootloader mode (no auto-reset).",
                    "warning",
                )

            # Step 1: download firmware
            self._emit("Fetching latest firmware release...", "dim")
            fw_dir = self._download_firmware()
            if not fw_dir:
                return

            # Step 2: optional erase
            if erase:
                self._emit("", "default")
                self._emit("Erasing flash...", "warning")
                if not self._run_esptool(self._erase_cmd(port)):
                    return

            # Step 3: flash
            self._emit("", "default")
            self._emit("Flashing firmware...", "success")
            if not self._run_esptool(self._flash_cmd(port, fw_dir)):
                return

            self._emit("", "default")
            self._emit("Flash complete! ESP32-C5 is rebooting.", "success")
            self.success = True

            # XIAO: reset USB hub so device re-enumerates after flash
            if self._board == "xiao":
                self._reset_usb_hub(port)

            # Save flashed firmware version for startup check
            if self._release_tag:
                try:
                    from .updater import save_local_fw_version
                    save_local_fw_version(self._release_tag)
                    self._emit(f"Firmware version saved: {self._release_tag}", "dim")
                except Exception:
                    pass

        except Exception as exc:
            self._emit(f"ERROR: {exc}", "error")
            log.exception("Flash failed")
        finally:
            self.done = True
            self.running = False

    # ------------------------------------------------------------------
    # USB hub reset (XIAO post-flash)
    # ------------------------------------------------------------------

    def _reset_usb_hub(self, port: str) -> None:
        """Reset the parent USB hub so XIAO re-enumerates after flash.

        Native USB-Serial/JTAG devices don't auto-reconnect through
        some USB hubs (e.g. QinHeng CH9102) after a flash cycle.
        Toggling the hub's 'authorized' sysfs attribute forces
        re-enumeration without a full system reboot.
        """
        import glob
        import time as _time

        self._emit("Resetting USB hub for re-enumeration...", "dim")

        # Find the sysfs parent hub for the port (e.g. /dev/ttyACM1)
        # Walk /sys/bus/usb/devices/*/tty/* to find matching ttyACMx
        tty_name = os.path.basename(port)  # e.g. "ttyACM1"
        hub_path = None

        for tty_path in glob.glob("/sys/bus/usb/devices/*/tty/" + tty_name):
            # tty_path like /sys/bus/usb/devices/1-1.2:1.0/tty/ttyACM1
            # Parent USB device: 1-1.2, parent hub: 1-1
            usb_intf = tty_path.split("/sys/bus/usb/devices/")[1].split("/tty/")[0]
            usb_dev = usb_intf.split(":")[0]  # "1-1.2"
            # Parent hub = strip last .N segment
            if "." in usb_dev:
                parent_hub = usb_dev.rsplit(".", 1)[0]  # "1-1"
                candidate = f"/sys/bus/usb/devices/{parent_hub}/authorized"
                if os.path.exists(candidate):
                    hub_path = candidate
            break

        if not hub_path:
            # Fallback: try common path
            fallback = "/sys/bus/usb/devices/1-1/authorized"
            if os.path.exists(fallback):
                hub_path = fallback

        if not hub_path:
            self._emit("USB hub reset skipped (sysfs path not found).", "dim")
            return

        try:
            # Deauthorize → wait → reauthorize
            with open(hub_path, "w") as f:
                f.write("0")
            _time.sleep(2)
            with open(hub_path, "w") as f:
                f.write("1")
            _time.sleep(3)
            self._emit("USB hub reset complete.", "success")
        except PermissionError:
            # Try with subprocess (sudo)
            try:
                subprocess.run(
                    ["sudo", "sh", "-c",
                     f"echo 0 > {hub_path} && sleep 2 && echo 1 > {hub_path}"],
                    timeout=10,
                )
                _time.sleep(3)
                self._emit("USB hub reset complete.", "success")
            except Exception as exc:
                self._emit(f"USB hub reset failed: {exc}", "warning")
                self._emit("You may need to reboot for device to reconnect.", "warning")
        except Exception as exc:
            self._emit(f"USB hub reset failed: {exc}", "warning")

    # ------------------------------------------------------------------
    # Firmware download
    # ------------------------------------------------------------------

    def _download_firmware(self) -> Optional[str]:
        os.makedirs(FIRMWARE_DIR, exist_ok=True)
        profile = self._profile
        try:
            # Fetch latest release metadata
            req = Request(FIRMWARE_RELEASE_URL)
            req.add_header("User-Agent", "JanOS-App")
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            tag = data["tag_name"]
            self._release_tag = tag
            self._emit(f"Latest release: {tag}", "success")

            # Determine which ZIP to download based on board
            zip_suffix = "-xiao" if self._board == "xiao" else ""
            zip_url = None
            target_prefix = f"projectzerobylocosp{zip_suffix}"

            for asset in data.get("assets", []):
                name = asset["name"].lower()
                if name.startswith(target_prefix) \
                        and name.endswith(".zip") \
                        and "fap" not in name and "with" not in name:
                    zip_url = asset["browser_download_url"]
                    break

            if not zip_url:
                # Fallback: try versioned name
                ver = tag.lstrip("v")
                zip_url = (
                    f"https://github.com/LOCOSP/projectZero/releases"
                    f"/download/{tag}/projectZerobyLOCOSP{zip_suffix}-{ver}.zip"
                )

            self._emit(f"Downloading {os.path.basename(zip_url)}...", "dim")

            # Download with progress
            zip_path = os.path.join(FIRMWARE_DIR, "firmware.zip")
            req = Request(zip_url)
            req.add_header("User-Agent", "JanOS-App")
            with urlopen(req, timeout=120) as resp:
                total = resp.headers.get("Content-Length")
                total = int(total) if total else None
                downloaded = 0
                last_pct = -1
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            if pct >= last_pct + 10:
                                last_pct = pct
                                self._emit(
                                    f"  {downloaded // 1024}KB / "
                                    f"{total // 1024}KB ({pct}%)", "dim",
                                )

            self._emit("Download complete. Extracting...", "dim")

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(FIRMWARE_DIR)

            # Verify required files
            required = list(profile["offsets"].keys())
            missing = [
                f for f in required
                if not os.path.exists(os.path.join(FIRMWARE_DIR, f))
            ]
            if missing:
                self._emit(
                    f"Missing firmware files: {', '.join(missing)}", "error",
                )
                return None

            self._emit("Firmware files ready.", "success")
            return FIRMWARE_DIR

        except URLError as exc:
            self._emit(f"Download failed: {exc}", "error")
            return None
        except Exception as exc:
            self._emit(f"Download error: {exc}", "error")
            return None

    # ------------------------------------------------------------------
    # esptool commands
    # ------------------------------------------------------------------

    def _esptool_prefix(self) -> list:
        """Return command prefix for esptool."""
        try:
            import esptool  # noqa
            return [sys.executable, "-m", "esptool"]
        except ImportError:
            pass
        for name in ("esptool", "esptool.py"):
            path = shutil.which(name)
            if path:
                return [path]
        return [sys.executable, "-m", "esptool"]

    def _erase_cmd(self, port: str) -> list:
        profile = self._profile
        return [
            *self._esptool_prefix(),
            "-p", port, "-b", str(profile["baud"]),
            "--before", profile["before"],
            "--after", "no-reset",
            "--chip", FLASH_CHIP,
            "erase-flash",
        ]

    def _flash_cmd(self, port: str, fw_dir: str) -> list:
        profile = self._profile
        after = "no-reset" if self._board == "xiao" else "hard-reset"
        cmd = [
            *self._esptool_prefix(),
            "-p", port, "-b", str(profile["baud"]),
            "--before", profile["before"],
            "--after", after,
            "--chip", FLASH_CHIP,
            "write-flash",
            "--flash-mode", FLASH_MODE,
            "--flash-freq", FLASH_FREQ,
            "--flash-size", "detect",
        ]
        for filename, offset in profile["offsets"].items():
            cmd.extend([offset, os.path.join(fw_dir, filename)])
        return cmd

    def _run_esptool(self, cmd: list) -> bool:
        """Run an esptool command, streaming output to the queue."""
        short = " ".join(cmd[cmd.index("--chip"):]) if "--chip" in cmd else " ".join(cmd[-6:])
        self._emit(f"$ esptool {short}", "dim")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                line = _ANSI_RE.sub('', line).rstrip()
                if not line:
                    continue
                ll = line.lower()
                if "error" in ll or "fail" in ll:
                    attr = "error"
                elif "writing" in ll or "%" in line:
                    attr = "success"
                elif "done" in ll or "success" in ll or "hash" in ll or "leaving" in ll:
                    attr = "success"
                else:
                    attr = "dim"
                self._emit(f"  {line}", attr)
            proc.wait()
            if proc.returncode != 0:
                self._emit(
                    f"esptool exited with code {proc.returncode}", "error",
                )
                return False
            return True
        except FileNotFoundError:
            self._emit(
                "esptool not found! Install: pip install esptool", "error",
            )
            return False
        except Exception as exc:
            self._emit(f"esptool error: {exc}", "error")
            return False
