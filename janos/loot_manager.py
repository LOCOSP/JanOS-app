"""Loot manager — persists all captured data to disk.

Session directory layout:
    <app_dir>/loot/<YYYY-MM-DD_HH-MM-SS>/
        serial_full.log          – every line from ESP32 (timestamped)
        scan_results.csv         – networks found during scan
        sniffer_aps.csv          – access points from sniffer
        sniffer_probes.csv       – probe requests captured
        handshakes/
            <ssid>_<bssid>.txt   – handshake metadata from serial
        portal_passwords.log     – portal form submissions
        evil_twin_capture.log    – evil twin captured data
        attacks.log              – attack start/stop events
"""

import csv
import io
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .app_state import AppState, Network, SnifferAP, ProbeEntry

log = logging.getLogger(__name__)


class LootManager:
    """Manages a per-session loot directory and auto-saves captured data."""

    def __init__(self, app_dir: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._base = Path(app_dir) / "loot"
        self._session = self._base / ts
        self._serial_fh: Optional[io.TextIOWrapper] = None
        self._handshake_dir: Optional[Path] = None
        self._session_active = False

        # Handshake parser state
        self._hs_buffer: List[str] = []
        self._hs_collecting = False

        try:
            self._session.mkdir(parents=True, exist_ok=True)
            (self._session / "handshakes").mkdir(exist_ok=True)
            self._handshake_dir = self._session / "handshakes"
            self._serial_fh = open(
                self._session / "serial_full.log", "a", encoding="utf-8"
            )
            self._session_active = True
            log.info("Loot session: %s", self._session)
        except OSError as exc:
            log.error("Cannot create loot directory: %s", exc)

    @property
    def session_path(self) -> str:
        return str(self._session)

    @property
    def active(self) -> bool:
        return self._session_active

    # ------------------------------------------------------------------
    # Full serial log
    # ------------------------------------------------------------------

    def log_serial(self, line: str) -> None:
        """Append a timestamped serial line to the full log."""
        if not self._serial_fh:
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            self._serial_fh.write(f"[{ts}] {line}\n")
            self._serial_fh.flush()
        except OSError:
            pass

        # Check for handshake data in serial stream
        self._detect_handshake(line)

    # ------------------------------------------------------------------
    # Handshake detection from serial stream
    # ------------------------------------------------------------------

    # Keywords that START handshake collection
    _HS_START_KW = ("message pair:", "message_pair:", "ANonce present")

    # Keywords that belong to a handshake block (keep collecting)
    _HS_RELATED_KW = (
        "message pair:", "message_pair:", "ANonce present", "SNonce present",
        "Key MIC", "AP MAC:", "STA MAC:", "EAPOL data:", "SSID:",
        "HANDSHAKE IS COMPLETE", "HANDSHAKE IS VALID",
        "Handshake #", "captured!", "Created ", "Failed ",
        "Cycle:", "networks captured", "attack cleanup",
        "cleanup complete", "task finished",
        "=====",  # separator lines
    )

    # Keywords that END handshake collection (save after this line)
    _HS_END_KW = ("task finished", "cleanup complete")

    def _detect_handshake(self, line: str) -> None:
        """Collect handshake metadata from serial and save when complete.

        Collects from 'message pair:' through 'task finished', saves ONCE.
        """
        stripped = line.strip()

        # Start collecting on handshake start indicators
        if not self._hs_collecting:
            if any(kw in stripped for kw in self._HS_START_KW):
                self._hs_collecting = True
                self._hs_buffer = [stripped]
            return

        # We are collecting — check if this line belongs to the block
        if any(kw in stripped for kw in self._HS_RELATED_KW) or not stripped:
            self._hs_buffer.append(stripped)
            # Check for end marker
            if any(kw in stripped for kw in self._HS_END_KW):
                self._save_handshake_buffer()
        else:
            # Unrelated line — save what we have and stop
            self._save_handshake_buffer()

    def _save_handshake_buffer(self) -> None:
        """Write collected handshake metadata to a file."""
        if not self._hs_buffer or not self._handshake_dir:
            self._hs_collecting = False
            self._hs_buffer = []
            return

        # Extract SSID and BSSID for filename
        ssid = "unknown"
        bssid = "unknown"
        for line in self._hs_buffer:
            if "SSID:" in line:
                parts = line.split("SSID:")
                if len(parts) > 1:
                    ssid = parts[1].strip().split()[0].strip(",")
            if "AP MAC:" in line:
                parts = line.split("AP MAC:")
                if len(parts) > 1:
                    bssid = parts[1].strip().replace(":", "")[:12]

        ts = datetime.now().strftime("%H%M%S")
        safe_ssid = "".join(c if c.isalnum() or c in "-_" else "_" for c in ssid)
        filename = f"{safe_ssid}_{bssid}_{ts}.txt"
        filepath = self._handshake_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(f"# Handshake captured at {datetime.now().isoformat()}\n")
                fh.write(f"# SSID: {ssid}\n")
                fh.write(f"# BSSID: {bssid}\n\n")
                for line in self._hs_buffer:
                    fh.write(line + "\n")
            log.info("Handshake saved: %s", filepath)
        except OSError as exc:
            log.error("Cannot save handshake: %s", exc)

        self._hs_collecting = False
        self._hs_buffer = []

    # ------------------------------------------------------------------
    # Scan results
    # ------------------------------------------------------------------

    def save_scan_results(self, networks: List[Network]) -> None:
        """Save scan results as CSV."""
        if not self._session_active or not networks:
            return
        filepath = self._session / "scan_results.csv"
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["#", "SSID", "BSSID", "Channel", "Auth", "RSSI", "Band", "Vendor"])
                for n in networks:
                    writer.writerow([
                        n.index, n.ssid, n.bssid, n.channel,
                        n.auth, n.rssi, n.band, n.vendor,
                    ])
            log.info("Scan results saved: %d networks", len(networks))
        except OSError as exc:
            log.error("Cannot save scan results: %s", exc)

    # ------------------------------------------------------------------
    # Sniffer results
    # ------------------------------------------------------------------

    def save_sniffer_aps(self, aps: List[SnifferAP]) -> None:
        """Save sniffer AP results as CSV."""
        if not self._session_active or not aps:
            return
        filepath = self._session / "sniffer_aps.csv"
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["SSID", "Channel", "Clients", "Client_MACs"])
                for ap in aps:
                    writer.writerow([
                        ap.ssid, ap.channel, ap.client_count,
                        ";".join(ap.clients),
                    ])
            log.info("Sniffer APs saved: %d", len(aps))
        except OSError as exc:
            log.error("Cannot save sniffer APs: %s", exc)

    def save_sniffer_probes(self, probes: List[ProbeEntry]) -> None:
        """Save probe requests as CSV."""
        if not self._session_active or not probes:
            return
        filepath = self._session / "sniffer_probes.csv"
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["SSID", "MAC"])
                for p in probes:
                    writer.writerow([p.ssid, p.mac])
            log.info("Sniffer probes saved: %d", len(probes))
        except OSError as exc:
            log.error("Cannot save sniffer probes: %s", exc)

    # ------------------------------------------------------------------
    # Portal
    # ------------------------------------------------------------------

    def save_portal_event(self, line: str) -> None:
        """Append a portal password/form submission line."""
        if not self._session_active:
            return
        filepath = self._session / "portal_passwords.log"
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            with open(filepath, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {line}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Evil Twin
    # ------------------------------------------------------------------

    def save_evil_twin_event(self, line: str) -> None:
        """Append an evil twin capture line."""
        if not self._session_active:
            return
        filepath = self._session / "evil_twin_capture.log"
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            with open(filepath, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {line}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Attack events
    # ------------------------------------------------------------------

    def log_attack_event(self, event: str) -> None:
        """Log attack start/stop/result events."""
        if not self._session_active:
            return
        filepath = self._session / "attacks.log"
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            with open(filepath, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {event}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close file handles and write session summary."""
        if self._serial_fh:
            try:
                self._serial_fh.close()
            except OSError:
                pass

        # Write session summary
        if self._session_active:
            try:
                summary = self._session / "session_info.txt"
                with open(summary, "w", encoding="utf-8") as fh:
                    fh.write(f"Session ended: {datetime.now().isoformat()}\n")
                    # List files in session
                    for f in sorted(self._session.rglob("*")):
                        if f.is_file() and f.name != "session_info.txt":
                            size = f.stat().st_size
                            fh.write(f"  {f.relative_to(self._session)} ({size} bytes)\n")
            except OSError:
                pass
