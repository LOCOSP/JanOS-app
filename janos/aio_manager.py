"""AIO v2 module control — wrapper around aiov2_ctl CLI tool."""

import logging
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

FEATURES = ("gps", "lora", "sdr", "usb")


class AioManager:
    """Interface to HackerGadgets AIO v2 (aiov2_ctl) for GPIO control."""

    @staticmethod
    def is_installed() -> bool:
        return shutil.which("aiov2_ctl") is not None

    @staticmethod
    def get_status() -> Optional[dict]:
        """Query aiov2_ctl --status and parse interface states.

        Returns dict like {"gps": True, "lora": False, "sdr": False, "usb": True}
        or None on failure.
        """
        try:
            result = subprocess.run(
                ["aiov2_ctl", "--status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                timeout=5,
            )
            if result.returncode != 0:
                log.warning("aiov2_ctl --status failed: %s", result.stderr.strip())
                return None

            status = {}
            for line in result.stdout.splitlines():
                ll = line.lower()
                for feat in FEATURES:
                    if feat in ll:
                        status[feat] = "on" in ll
                        break
            return status if status else None

        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            log.warning("aiov2_ctl --status timed out")
            return None
        except Exception as exc:
            log.warning("aiov2_ctl error: %s", exc)
            return None

    @staticmethod
    def toggle(feature: str, on: bool) -> bool:
        """Toggle an AIO feature on or off. Returns True on success.

        ``aiov2_ctl`` spawns sub-processes (pinctrl, sudo systemctl)
        that inherit stdio.  We must isolate stdin so they cannot
        read from the terminal that urwid controls, and use
        ``start_new_session`` to fully detach from the controlling tty.
        Timeout is 15 s because ``systemctl stop meshtasticd`` can be slow.
        """
        if feature not in FEATURES:
            return False
        action = "on" if on else "off"
        try:
            result = subprocess.run(
                ["aiov2_ctl", feature, action],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                timeout=15,
            )
            if result.returncode == 0:
                log.info("AIO %s → %s", feature, action)
                return True
            log.warning("aiov2_ctl %s %s failed: %s", feature, action,
                        result.stderr.strip())
            return False
        except Exception as exc:
            log.warning("aiov2_ctl toggle error: %s", exc)
            return False

    @staticmethod
    def install(callback: Callable[[str, str], None]) -> None:
        """Install aiov2_ctl from GitHub in a background thread.

        callback(line, attr) is called for each output line.
        """
        def _run():
            callback("Installing aiov2_ctl from GitHub...", "attack_active")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install",
                     "git+https://github.com/hackergadgets/aiov2_ctl.git"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        callback(f"  {line}", "dim")
                proc.wait()
                if proc.returncode == 0:
                    callback("aiov2_ctl installed successfully!", "success")
                else:
                    callback(f"Install failed (exit code {proc.returncode})", "error")
            except Exception as exc:
                callback(f"Install error: {exc}", "error")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
