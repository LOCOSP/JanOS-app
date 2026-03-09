"""Download firmware from GitHub and flash ESP32-C5 via esptool."""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from queue import Queue
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

import serial
import serial.tools.list_ports

from .config import (
    FLASH_BAUD, FLASH_CHIP, FLASH_MODE, FLASH_FREQ,
    FLASH_OFFSETS, FIRMWARE_RELEASE_URL, FIRMWARE_DIR,
)

log = logging.getLogger(__name__)

REQUIRED_BINS = list(FLASH_OFFSETS.keys())


class FlashManager:
    """Download firmware and flash ESP32-C5 with live progress via queue."""

    def __init__(self) -> None:
        self.queue: Queue = Queue()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.done = False
        self.success = False

    def _emit(self, line: str, attr: str = "default") -> None:
        self.queue.put((line, attr))

    def start(self, erase: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.done = False
        self.success = False
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(erase,), daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Main flash pipeline (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self, erase: bool) -> None:
        try:
            # Step 1: detect port
            self._emit("Hold BOOT button and plug/replug USB cable.", "warning")
            self._emit("Waiting for ESP32-C5 in ROM mode...", "warning")

            before = self._list_ports()
            port = self._wait_for_port(before, timeout=30)
            if not port:
                self._emit("No new serial port detected!", "error")
                return
            self._emit(f"Detected port: {port}", "success")

            # Step 2: download firmware
            self._emit("", "default")
            self._emit("Fetching latest firmware release...", "dim")
            fw_dir = self._download_firmware()
            if not fw_dir:
                return

            # Step 3: optional erase
            if erase:
                self._emit("", "default")
                self._emit("Erasing flash...", "warning")
                if not self._run_esptool(self._erase_cmd(port)):
                    return

            # Step 4: flash
            self._emit("", "default")
            self._emit("Flashing firmware...", "attack_active")
            if not self._run_esptool(self._flash_cmd(port, fw_dir)):
                return

            # Step 5: reset into app mode
            self._emit("", "default")
            self._reset_to_app(port)

            self._emit("", "default")
            self._emit("Flash complete! ESP32-C5 is rebooting.", "success")
            self.success = True

        except Exception as exc:
            self._emit(f"ERROR: {exc}", "error")
            log.exception("Flash failed")
        finally:
            self.done = True
            self.running = False

    # ------------------------------------------------------------------
    # Port detection
    # ------------------------------------------------------------------

    @staticmethod
    def _list_ports() -> set:
        return {p.device for p in serial.tools.list_ports.comports()}

    def _wait_for_port(self, before: set, timeout: float = 30) -> Optional[str]:
        t0 = time.time()
        last_msg = 0
        while time.time() - t0 < timeout:
            if not self.running:
                return None
            after = self._list_ports()
            new = after - before
            if new:
                return new.pop()
            elapsed = int(time.time() - t0)
            if elapsed > 0 and elapsed % 5 == 0 and elapsed != last_msg:
                last_msg = elapsed
                self._emit(f"  Waiting... ({elapsed}s / {int(timeout)}s)", "dim")
            time.sleep(0.2)
        return None

    # ------------------------------------------------------------------
    # Firmware download
    # ------------------------------------------------------------------

    def _download_firmware(self) -> Optional[str]:
        os.makedirs(FIRMWARE_DIR, exist_ok=True)
        try:
            # Fetch latest release metadata
            req = Request(FIRMWARE_RELEASE_URL)
            req.add_header("User-Agent", "JanOS-App")
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            tag = data["tag_name"]
            self._emit(f"Latest release: {tag}", "success")

            # Find firmware ZIP asset
            zip_url = None
            for asset in data.get("assets", []):
                name = asset["name"].lower()
                if "firmware" in name and name.endswith(".zip"):
                    zip_url = asset["browser_download_url"]
                    break

            if not zip_url:
                # Fallback URL pattern
                zip_url = (
                    f"https://github.com/LOCOSP/projectZero/releases"
                    f"/download/{tag}/esp32c5-firmware.zip"
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

            # Extract
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(FIRMWARE_DIR)

            # Verify required files
            missing = [
                f for f in REQUIRED_BINS
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
        # Prefer python -m esptool (works if installed in same env)
        try:
            import esptool  # noqa
            return [sys.executable, "-m", "esptool"]
        except ImportError:
            pass
        # Fallback to system esptool
        for name in ("esptool", "esptool.py"):
            path = shutil.which(name)
            if path:
                return [path]
        return [sys.executable, "-m", "esptool"]  # let it fail with clear error

    def _erase_cmd(self, port: str) -> list:
        return [
            *self._esptool_prefix(),
            "-p", port, "-b", str(FLASH_BAUD),
            "--before", "default-reset",
            "--after", "no_reset",
            "--chip", FLASH_CHIP,
            "erase_flash",
        ]

    def _flash_cmd(self, port: str, fw_dir: str) -> list:
        cmd = [
            *self._esptool_prefix(),
            "-p", port, "-b", str(FLASH_BAUD),
            "--before", "default-reset",
            "--after", "watchdog-reset",
            "--chip", FLASH_CHIP,
            "write_flash",
            "--flash-mode", FLASH_MODE,
            "--flash-freq", FLASH_FREQ,
            "--flash-size", "detect",
        ]
        for filename, offset in FLASH_OFFSETS.items():
            cmd.extend([offset, os.path.join(fw_dir, filename)])
        return cmd

    def _run_esptool(self, cmd: list) -> bool:
        """Run an esptool command, streaming output to the queue."""
        # Log a short version of the command
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
                line = line.rstrip()
                if not line:
                    continue
                ll = line.lower()
                if "error" in ll or "fail" in ll:
                    attr = "error"
                elif "writing" in ll or "%" in line:
                    attr = "attack_active"
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

    # ------------------------------------------------------------------
    # Post-flash reset
    # ------------------------------------------------------------------

    def _reset_to_app(self, port: str) -> None:
        """RTS/DTR pulse to boot into application (not ROM)."""
        self._emit("Resetting ESP32 to application mode...", "dim")
        try:
            with serial.Serial(port, 115200, timeout=0.1) as ser:
                ser.dtr = False
                time.sleep(0.06)
                ser.rts = True
                time.sleep(0.06)
                ser.rts = False
                time.sleep(0.06)
            self._emit("Reset pulse sent.", "success")
        except Exception as exc:
            self._emit(f"Auto-reset failed: {exc}", "warning")
            self._emit("Press RESET button on ESP32 manually.", "warning")
