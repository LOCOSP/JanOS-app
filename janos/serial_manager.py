"""Serial communication with ESP32-C5 device."""

import os
import sys
import time
import logging
from typing import List, Optional, Callable

import serial

from .config import BAUD_RATE, READ_TIMEOUT, SCAN_TIMEOUT, CRASH_KEYWORDS

log = logging.getLogger(__name__)


class SerialLineBuffer:
    """Accumulate raw bytes and yield complete newline-terminated lines.

    Used with urwid's ``watch_file`` callback where reads are non-blocking
    and may deliver partial lines.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, raw: bytes) -> List[str]:
        self._buf += raw
        lines: List[str] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                lines.append(decoded)
        return lines


class SerialManager:
    """Manage the serial connection to an ESP32-C5 device."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.serial_conn: Optional[serial.Serial] = None
        self.baud_rate = BAUD_RATE
        self.line_buffer = SerialLineBuffer()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Open the serial port. Raises on failure."""
        if not os.path.exists(self.device):
            raise FileNotFoundError(f"Device {self.device} does not exist")

        if not os.access(self.device, os.R_OK | os.W_OK):
            raise PermissionError(
                f"No read/write access to '{self.device}'. "
                "Try: sudo usermod -a -G dialout $USER"
            )

        self.serial_conn = serial.Serial(
            port=self.device,
            baudrate=self.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT,
            write_timeout=2,
        )
        self.serial_conn.reset_input_buffer()
        self.serial_conn.reset_output_buffer()
        log.info("Serial port %s opened at %d baud", self.device, self.baud_rate)

    def close(self) -> None:
        if self.serial_conn:
            self.serial_conn.close()
            self.serial_conn = None

    @property
    def is_open(self) -> bool:
        return self.serial_conn is not None and self.serial_conn.is_open

    @property
    def fd(self) -> int:
        """Return the file descriptor for use with urwid watch_file."""
        if self.serial_conn is None:
            raise RuntimeError("Serial port not open")
        return self.serial_conn.fileno()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def send_command(self, command: str) -> None:
        if not self.serial_conn:
            log.error("Serial not connected — cannot send %r", command)
            return
        try:
            self.serial_conn.write((command + "\r\n").encode("utf-8"))
            self.serial_conn.flush()
            time.sleep(0.1)
            log.debug("TX: %s", command)
        except Exception as exc:
            log.error("Send error: %s", exc)

    def read_available(self) -> List[str]:
        """Non-blocking read: grab whatever bytes are waiting and return
        complete lines via the internal line buffer."""
        if not self.serial_conn:
            return []
        waiting = self.serial_conn.in_waiting
        if waiting <= 0:
            return []
        raw = self.serial_conn.read(waiting)
        return self.line_buffer.feed(raw)

    def read_response(self, timeout: float = SCAN_TIMEOUT, idle_timeout: float = 1.5) -> List[str]:
        """Blocking read with timeout — kept for legacy / direct-call use."""
        if not self.serial_conn:
            return []

        lines: List[str] = []
        start = time.time()
        last_data = time.time()

        while time.time() - start < timeout:
            if self.serial_conn.in_waiting:
                try:
                    line = self.serial_conn.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        lines.append(line)
                        last_data = time.time()
                except Exception:
                    continue
            else:
                if lines and (time.time() - last_data) >= idle_timeout:
                    break
                time.sleep(0.05)

        return lines

    # ------------------------------------------------------------------
    # Crash detection helper
    # ------------------------------------------------------------------

    @staticmethod
    def is_crash_line(line: str) -> bool:
        return any(kw in line for kw in CRASH_KEYWORDS)
