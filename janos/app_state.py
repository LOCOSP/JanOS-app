"""Centralized mutable application state."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Network:
    index: str = ""
    ssid: str = ""
    vendor: str = ""
    bssid: str = ""
    channel: str = ""
    auth: str = ""
    rssi: str = ""
    band: str = ""


@dataclass
class SnifferAP:
    ssid: str = ""
    channel: int = 0
    client_count: int = 0
    clients: List[str] = field(default_factory=list)


@dataclass
class ProbeEntry:
    ssid: str = ""
    mac: str = ""


@dataclass
class AppState:
    # Device
    device: str = ""
    connected: bool = False

    # Scan
    networks: List[Network] = field(default_factory=list)
    scan_done: bool = False
    selected_networks: str = ""

    # Sniffer
    sniffer_running: bool = False
    sniffer_packets: int = 0
    sniffer_aps: List[SnifferAP] = field(default_factory=list)
    sniffer_probes: List[ProbeEntry] = field(default_factory=list)
    sniffer_buffer: List[str] = field(default_factory=list)

    # Attacks
    attack_running: bool = False
    blackout_running: bool = False
    sae_overflow_running: bool = False
    handshake_running: bool = False

    # Portal
    portal_running: bool = False
    portal_ssid: str = ""
    portal_html_files: List[str] = field(default_factory=list)
    selected_html_index: int = -1
    selected_html_name: str = ""
    submitted_forms: int = 0
    last_submitted_data: str = ""
    portal_client_count: int = 0
    portal_log: List[str] = field(default_factory=list)

    # Evil Twin
    evil_twin_running: bool = False
    evil_twin_ssid: str = ""
    evil_twin_captured_data: List[str] = field(default_factory=list)
    evil_twin_client_count: int = 0
    evil_twin_log: List[str] = field(default_factory=list)

    # GPS
    gps_available: bool = False
    gps_fix_valid: bool = False
    gps_latitude: float = 0.0
    gps_longitude: float = 0.0
    gps_altitude: float = 0.0
    gps_satellites: int = 0
    gps_fix_quality: int = 0
    gps_hdop: float = 99.9

    # Runtime
    start_time: float = 0.0
    firmware_crashed: bool = False
    crash_message: str = ""

    def any_attack_running(self) -> bool:
        return any([
            self.attack_running,
            self.blackout_running,
            self.sae_overflow_running,
            self.handshake_running,
            self.portal_running,
            self.evil_twin_running,
        ])

    def stop_all(self) -> None:
        """Reset all running flags. ESP32 'stop' halts everything."""
        self.attack_running = False
        self.blackout_running = False
        self.sae_overflow_running = False
        self.handshake_running = False
        self.sniffer_running = False
        self.portal_running = False
        self.evil_twin_running = False

    def reset_sniffer(self) -> None:
        self.sniffer_running = False
        self.sniffer_packets = 0
        self.sniffer_aps.clear()
        self.sniffer_probes.clear()
        self.sniffer_buffer.clear()

    def reset_portal(self) -> None:
        self.portal_running = False
        self.portal_ssid = ""
        self.submitted_forms = 0
        self.last_submitted_data = ""
        self.portal_client_count = 0
        self.portal_log.clear()

    def reset_evil_twin(self) -> None:
        self.evil_twin_running = False
        self.evil_twin_ssid = ""
        self.evil_twin_captured_data.clear()
        self.evil_twin_client_count = 0
        self.evil_twin_log.clear()
