"""Download firmware from GitHub and flash ESP32-C5 via esptool."""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from queue import Queue
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

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
        self._release_tag: str = ""

    def _emit(self, line: str, attr: str = "default") -> None:
        self.queue.put((line, attr))

    def start(self, port: str, erase: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.done = False
        self.success = False
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(port, erase), daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Main flash pipeline (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self, port: str, erase: bool) -> None:
        try:
            self._emit(f"Target port: {port}", "dim")

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
            self._emit("Flashing firmware...", "attack_active")
            if not self._run_esptool(self._flash_cmd(port, fw_dir)):
                return

            self._emit("", "default")
            self._emit("Flash complete! ESP32-C5 is rebooting.", "success")
            self.success = True

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
            self._release_tag = tag
            self._emit(f"Latest release: {tag}", "success")

            # Find firmware ZIP asset (projectZero-X.Y.Z.zip, not fap/bundle)
            zip_url = None
            for asset in data.get("assets", []):
                name = asset["name"].lower()
                if name.startswith("projectzero") and name.endswith(".zip") \
                        and "fap" not in name and "bundle" not in name \
                        and "with" not in name:
                    zip_url = asset["browser_download_url"]
                    break

            if not zip_url:
                # Fallback: try versioned name
                ver = tag.lstrip("v")
                zip_url = (
                    f"https://github.com/LOCOSP/projectZero/releases"
                    f"/download/{tag}/projectZero-{ver}.zip"
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
            "--after", "hard_reset",
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
