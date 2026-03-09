"""Loot manager — persists all captured data to disk.

Session directory layout:
    <app_dir>/loot/<YYYY-MM-DD_HH-MM-SS>/
        serial_full.log          – every line from ESP32 (timestamped)
        scan_results.csv         – networks found during scan
        sniffer_aps.csv          – access points from sniffer
        sniffer_probes.csv       – probe requests captured
        handshakes/
            <ssid>_<bssid>.txt   – handshake metadata from serial
            <ssid>_<bssid>.pcap  – real pcap (from start_handshake_serial)
            <ssid>_<bssid>.hccapx – hashcat format (from start_handshake_serial)
        portal_passwords.log     – portal form submissions
        evil_twin_capture.log    – evil twin captured data
        attacks.log              – attack start/stop events
"""

import base64
import csv
import io
import json
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

    def __init__(self, app_dir: str, gps_manager=None) -> None:
        self._gps = gps_manager  # Optional GpsManager for geo-tagging
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._base = Path(app_dir) / "loot"
        self._session = self._base / ts
        self._serial_fh: Optional[io.TextIOWrapper] = None
        self._handshake_dir: Optional[Path] = None
        self._session_active = False

        # Handshake metadata parser state (from serial log keywords)
        self._hs_buffer: List[str] = []
        self._hs_collecting = False

        # PCAP base64 parser state (from start_handshake_serial command)
        # Firmware outputs:
        #   --- PCAP BEGIN ---
        #   <base64 lines>
        #   --- PCAP END ---
        #   PCAP_SIZE: <N>
        #   --- HCCAPX BEGIN ---
        #   <base64 lines>
        #   --- HCCAPX END ---
        #   SSID: <ssid>  AP: <bssid>
        self._pcap_collecting = False
        self._pcap_b64_lines: List[str] = []
        self._hccapx_collecting = False
        self._hccapx_b64_lines: List[str] = []
        self._pcap_meta_ssid = "unknown"
        self._pcap_meta_bssid = "unknown"

        # Aggregate loot database
        self._db_path = self._base / "loot_db.json"
        self._db: dict = {}
        self._session_key = ts

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

        # Load or build aggregate loot database
        self._db = self._load_or_build_db()

    @property
    def session_path(self) -> str:
        return str(self._session)

    @property
    def active(self) -> bool:
        return self._session_active

    # ------------------------------------------------------------------
    # Aggregate loot database
    # ------------------------------------------------------------------

    def _load_or_build_db(self) -> dict:
        """Load loot_db.json, or rebuild from session dirs if missing/corrupt."""
        if self._db_path.is_file():
            try:
                with open(self._db_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and "version" in data and "sessions" in data:
                    log.info("Loot DB loaded: %d sessions", len(data.get("sessions", {})))
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Loot DB corrupted (%s), rebuilding", exc)
        return self._rebuild_db()

    def _rebuild_db(self) -> dict:
        """Scan all loot session directories and build the DB from scratch."""
        db: dict = {"version": 1, "sessions": {}, "totals": {}}
        if not self._base.is_dir():
            return db
        for entry in sorted(self._base.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if len(name) >= 19 and name[4] == "-" and name[7] == "_" and name[10] == "-":
                db["sessions"][name] = self._scan_session_dir(entry)
        self._recalc_totals(db)
        self._save_db(db)
        log.info("Loot DB rebuilt: %d sessions", len(db["sessions"]))
        return db

    def _scan_session_dir(self, session_path: Path) -> dict:
        """Count loot items in a single session directory."""
        counts = {"pcap": 0, "hccapx": 0, "passwords": 0, "et_captures": 0}
        hs_dir = session_path / "handshakes"
        if hs_dir.is_dir():
            try:
                for f in hs_dir.iterdir():
                    if f.suffix == ".pcap":
                        counts["pcap"] += 1
                    elif f.suffix == ".hccapx":
                        counts["hccapx"] += 1
            except OSError:
                pass
        pw_file = session_path / "portal_passwords.log"
        if pw_file.is_file():
            try:
                counts["passwords"] = sum(1 for _ in open(pw_file, encoding="utf-8"))
            except OSError:
                pass
        et_file = session_path / "evil_twin_capture.log"
        if et_file.is_file():
            try:
                counts["et_captures"] = sum(1 for _ in open(et_file, encoding="utf-8"))
            except OSError:
                pass
        return counts

    def _recalc_totals(self, db: dict) -> None:
        """Recalculate totals from all session entries."""
        keys = ("pcap", "hccapx", "passwords", "et_captures")
        totals: dict = {k: 0 for k in keys}
        totals["sessions"] = len(db["sessions"])
        for session_counts in db["sessions"].values():
            for k in keys:
                totals[k] += session_counts.get(k, 0)
        db["totals"] = totals

    def _save_db(self, db: dict) -> None:
        """Write the DB to loot_db.json atomically."""
        try:
            tmp = self._db_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(db, fh, indent=2)
            tmp.replace(self._db_path)
        except OSError as exc:
            log.error("Cannot save loot DB: %s", exc)

    def update_session_loot(self) -> None:
        """Rescan current session and update aggregate DB."""
        if not self._session_active:
            return
        self._db["sessions"][self._session_key] = self._scan_session_dir(self._session)
        self._recalc_totals(self._db)
        self._save_db(self._db)

    @property
    def loot_totals(self) -> dict:
        """Aggregate totals across all sessions."""
        return self._db.get("totals", {})

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

        # Check for handshake metadata in serial stream
        self._detect_handshake(line)
        # Check for pcap base64 data from start_handshake_serial
        self._detect_pcap_stream(line)

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
                fh.write(f"# BSSID: {bssid}\n")
                fh.write(self._gps_header_lines())
                fh.write("\n")
                for line in self._hs_buffer:
                    fh.write(line + "\n")
            log.info("Handshake saved: %s", filepath)
            self._save_gps_sidecar(filepath)
            self.update_session_loot()
        except OSError as exc:
            log.error("Cannot save handshake: %s", exc)

        self._hs_collecting = False
        self._hs_buffer = []

    # ------------------------------------------------------------------
    # PCAP base64 stream parser (start_handshake_serial output)
    # ------------------------------------------------------------------

    def _detect_pcap_stream(self, line: str) -> None:
        """Parse base64-encoded pcap/hccapx blocks from serial stream.

        Triggered by start_handshake_serial firmware command which outputs:
            --- PCAP BEGIN ---
            <base64 lines>
            --- PCAP END ---
            PCAP_SIZE: <N>
            --- HCCAPX BEGIN ---
            <base64 lines>
            --- HCCAPX END ---
            SSID: <ssid>  AP: <bssid>
        """
        stripped = line.strip()

        # PCAP block
        if "--- PCAP BEGIN ---" in stripped:
            self._pcap_collecting = True
            self._pcap_b64_lines = []
            return

        if self._pcap_collecting:
            if "--- PCAP END ---" in stripped:
                self._pcap_collecting = False
            else:
                # Collect only valid base64 chars
                clean = stripped.replace(" ", "")
                if clean:
                    self._pcap_b64_lines.append(clean)
            return

        # HCCAPX block
        if "--- HCCAPX BEGIN ---" in stripped:
            self._hccapx_collecting = True
            self._hccapx_b64_lines = []
            return

        if self._hccapx_collecting:
            if "--- HCCAPX END ---" in stripped:
                self._hccapx_collecting = False
            else:
                clean = stripped.replace(" ", "")
                if clean:
                    self._hccapx_b64_lines.append(clean)
            return

        # Metadata line: firmware prints SSID and AP MAC after the blocks
        if "SSID:" in stripped and "AP:" in stripped:
            try:
                parts = stripped.split("SSID:")
                if len(parts) > 1:
                    rest = parts[1].strip()
                    if "AP:" in rest:
                        ssid_part, ap_part = rest.split("AP:", 1)
                        self._pcap_meta_ssid = ssid_part.strip()
                        self._pcap_meta_bssid = ap_part.strip().replace(":", "")[:12]
                    else:
                        self._pcap_meta_ssid = rest.strip().split()[0]
            except Exception:
                pass
            # Save when we have both pcap and hccapx (or at least pcap)
            if self._pcap_b64_lines:
                self._save_pcap_from_b64()
            return

        # Fallback: if we got hccapx end and pcap is ready, save after short wait
        # (in case firmware doesn't print SSID/AP line)
        if self._pcap_b64_lines and self._hccapx_b64_lines and not self._hccapx_collecting:
            # Check if this is an unrelated line that signals end of block
            if not any(c in stripped for c in ("BEGIN", "END", "SIZE:", "PCAP", "HCCAPX")):
                self._save_pcap_from_b64()

    def _save_pcap_from_b64(self) -> None:
        """Decode and save accumulated base64 pcap/hccapx data as binary files."""
        if not self._handshake_dir or not self._pcap_b64_lines:
            self._pcap_b64_lines = []
            self._hccapx_b64_lines = []
            self._pcap_meta_ssid = "unknown"
            self._pcap_meta_bssid = "unknown"
            return

        ts = datetime.now().strftime("%H%M%S")
        safe_ssid = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in self._pcap_meta_ssid
        )
        base_name = f"{safe_ssid}_{self._pcap_meta_bssid}_{ts}"

        # Save .pcap
        try:
            pcap_data = base64.b64decode("".join(self._pcap_b64_lines))
            pcap_path = self._handshake_dir / f"{base_name}.pcap"
            with open(pcap_path, "wb") as fh:
                fh.write(pcap_data)
            log.info("PCAP saved: %s (%d bytes)", pcap_path, len(pcap_data))
            self._save_gps_sidecar(pcap_path)
        except Exception as exc:
            log.error("Cannot save PCAP: %s", exc)

        # Save .hccapx (if present)
        if self._hccapx_b64_lines:
            try:
                hccapx_data = base64.b64decode("".join(self._hccapx_b64_lines))
                hccapx_path = self._handshake_dir / f"{base_name}.hccapx"
                with open(hccapx_path, "wb") as fh:
                    fh.write(hccapx_data)
                log.info("HCCAPX saved: %s (%d bytes)", hccapx_path, len(hccapx_data))
                self._save_gps_sidecar(hccapx_path)
            except Exception as exc:
                log.error("Cannot save HCCAPX: %s", exc)

        # Reset state and update DB
        self._pcap_b64_lines = []
        self._hccapx_b64_lines = []
        self._pcap_meta_ssid = "unknown"
        self._pcap_meta_bssid = "unknown"
        self.update_session_loot()

    # ------------------------------------------------------------------
    # GPS sidecar (Pwnagotchi-compatible .gps.json)
    # ------------------------------------------------------------------

    def _save_gps_sidecar(self, base_path: Path) -> None:
        """Write a .gps.json sidecar alongside a capture file.

        Creates e.g. MyWiFi_AABB_143022.pcap.gps.json with raw GPS fix.
        Loot always contains full (unmasked) data.
        """
        if not self._gps or not self._gps.available:
            return
        fix = self._gps.fix
        if not fix.valid:
            return
        geo_path = base_path.parent / (base_path.name + ".gps.json")
        try:
            data = {
                "Latitude": round(fix.latitude, 7),
                "Longitude": round(fix.longitude, 7),
                "Altitude": round(fix.altitude, 1),
                "Date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Satellites": fix.satellites,
                "HDOP": round(fix.hdop, 1),
            }
            with open(geo_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            log.info("GPS sidecar: %s", geo_path)
        except Exception as exc:
            log.error("Cannot save GPS sidecar: %s", exc)

    def _gps_header_lines(self) -> str:
        """Return GPS header lines for .txt handshake files, or empty string."""
        if not self._gps or not self._gps.available:
            return ""
        fix = self._gps.fix
        if not fix.valid:
            return ""
        return (
            f"# GPS: {fix.latitude:.7f}, {fix.longitude:.7f}\n"
            f"# Alt: {fix.altitude:.1f}m  Sat: {fix.satellites}  HDOP: {fix.hdop:.1f}\n"
        )

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
            self.update_session_loot()
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
            self.update_session_loot()
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
        """Close file handles, write session summary, and update loot DB."""
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

            # Final DB update
            self.update_session_loot()
