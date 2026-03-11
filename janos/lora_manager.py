"""LoRa SX1262 control via SPI — sniffer, scanner, balloon tracker.

Uses LoRaRF library for direct SPI communication with SX1262 on AIO v2 board.
Background threads with queue-based output (same pattern as FlashManager).

Hardware: SX1262 on /dev/spidev1.0
  IRQ=GPIO26, Busy=GPIO24, Reset=GPIO25
  DIO2 as RF switch, DIO3 TCXO voltage
"""

import logging
import re
import threading
from queue import Queue
from typing import Optional

log = logging.getLogger(__name__)

# EU868 frequencies (Hz)
EU868_FREQUENCIES = [
    868_100_000,  # 868.1 MHz
    868_300_000,  # 868.3 MHz
    868_500_000,  # 868.5 MHz
    867_100_000,  # 867.1 MHz
    867_300_000,  # 867.3 MHz
    867_500_000,  # 867.5 MHz
    867_700_000,  # 867.7 MHz
    867_900_000,  # 867.9 MHz
]

# LoRa APRS frequencies (Hz) — 433 MHz ISM band
# Ref: SQ2CPA/LoRa_APRS_Balloon project
APRS_FREQUENCIES = [
    433_775_000,  # 433.775 MHz — primary APRS EU (SF12/CR5 "300bps")
    434_855_000,  # 434.855 MHz — secondary APRS (SF9/CR7 "1200bps")
    439_912_500,  # 439.9125 MHz — tertiary APRS
]

# APRS modulation profiles: (freq_hz, sf, cr, label)
APRS_PROFILES = [
    (433_775_000, 12, 5, "433.775 SF12/CR5 300bps"),
    (434_855_000,  9, 7, "434.855 SF9/CR7 1200bps"),
    (439_912_500, 12, 5, "439.9125 SF12/CR5 300bps"),
]

# Combined scanner frequencies (EU868 + APRS 433)
SCAN_FREQUENCIES = EU868_FREQUENCIES + APRS_FREQUENCIES

SPREADING_FACTORS = [7, 8, 9, 10, 11, 12]

# LoRa APRS packet prefix (3 bytes)
APRS_PREFIX = b"\x3c\xff\x01"

# Hardware config (from /etc/meshtasticd/config.yaml)
SPI_BUS = 1
SPI_CS = 0
SPI_SPEED = 7_800_000
PIN_RESET = 25
PIN_BUSY = 24
PIN_IRQ = 26  # DIO1


class LoRaManager:
    """Background LoRa operations with queue-based output."""

    def __init__(self) -> None:
        self.queue: Queue = Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.running = False
        self.mode = ""  # "sniffer", "scanner", "tracker"
        self.packets_received = 0

    def _emit(self, line: str, attr: str = "default") -> None:
        self.queue.put((line, attr))

    def _init_radio(self):
        """Initialize SX1262 via SPI. Returns LoRa object or None."""
        try:
            from LoRaRF import SX126x
        except ImportError:
            self._emit(
                "LoRaRF not installed! Run: pip install LoRaRF", "error",
            )
            return None

        try:
            lora = SX126x()

            # begin() calls setSpi() + setPins() + reset internally
            if not lora.begin(
                bus=SPI_BUS,
                cs=SPI_CS,
                reset=PIN_RESET,
                busy=PIN_BUSY,
                irq=PIN_IRQ,
            ):
                self._emit("SX1262 not detected on SPI bus", "error")
                return None

            # SX1262-specific: DIO2 as RF switch, DIO3 as TCXO voltage
            lora.setDio2RfSwitch(True)
            lora.setDio3TcxoCtrl(lora.DIO3_OUTPUT_1_8, 10)

            lora.setRxGain(lora.RX_GAIN_BOOSTED)
            self._emit("SX1262 radio initialized", "dim")
            return lora
        except Exception as exc:
            self._emit(f"Radio init failed: {exc}", "error")
            log.warning("SX1262 init failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Sniffer — listen on a single frequency/SF
    # ------------------------------------------------------------------

    def start_sniffer(
        self,
        freq: int = 868_100_000,
        sf: int = 7,
        bw: int = 125_000,
    ) -> None:
        """Start LoRa sniffer on given frequency and spreading factor."""
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self.mode = "sniffer"
        self.packets_received = 0
        self._thread = threading.Thread(
            target=self._run_sniffer,
            args=(freq, sf, bw),
            daemon=True,
        )
        self._thread.start()

    def _run_sniffer(self, freq: int, sf: int, bw: int) -> None:
        lora = self._init_radio()
        if not lora:
            self.running = False
            return
        try:
            lora.setFrequency(freq)
            lora.setLoRaModulation(sf, bw, 5, False)
            freq_mhz = freq / 1_000_000
            self._emit(
                f"Sniffer started: {freq_mhz:.1f} MHz SF{sf} BW{bw // 1000}k",
                "success",
            )

            while not self._stop_event.is_set():
                # RX_SINGLE + timeout avoids CPU spinning
                lora.request(lora.RX_SINGLE)
                lora.wait(2)  # 2s timeout
                if lora.available() > 0:
                    self._handle_packet(lora, f"{freq_mhz:.1f}MHz SF{sf}")
        except Exception as exc:
            self._emit(f"Sniffer error: {exc}", "error")
            log.error("LoRa sniffer error: %s", exc)
        finally:
            self._cleanup_radio(lora)
            self._emit("Sniffer stopped.", "dim")

    # ------------------------------------------------------------------
    # Scanner — cycle through EU868 + APRS 433 frequencies × SFs
    # ------------------------------------------------------------------

    def start_scanner(self) -> None:
        """Start scanning EU868 + APRS 433 frequencies × spreading factors."""
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self.mode = "scanner"
        self.packets_received = 0
        self._thread = threading.Thread(
            target=self._run_scanner, daemon=True,
        )
        self._thread.start()

    def _run_scanner(self) -> None:
        lora = self._init_radio()
        if not lora:
            self.running = False
            return
        try:
            total = len(SCAN_FREQUENCIES) * len(SPREADING_FACTORS)
            self._emit(
                f"Scanner: {len(EU868_FREQUENCIES)} EU868 + "
                f"{len(APRS_FREQUENCIES)} APRS freqs × "
                f"{len(SPREADING_FACTORS)} SFs = {total} combos",
                "success",
            )
            cycle = 0
            while not self._stop_event.is_set():
                cycle += 1
                self._emit(f"── Scan cycle {cycle} ──", "dim")
                for freq in SCAN_FREQUENCIES:
                    if self._stop_event.is_set():
                        break
                    for sf in SPREADING_FACTORS:
                        if self._stop_event.is_set():
                            break
                        freq_mhz = freq / 1_000_000
                        lora.setFrequency(freq)
                        lora.setLoRaModulation(sf, 125_000, 5, False)
                        lora.request(lora.RX_SINGLE)
                        lora.wait(0.5)  # 500ms per combo
                        if lora.available() > 0:
                            self._handle_packet(
                                lora, f"{freq_mhz:.1f}MHz SF{sf}",
                            )
        except Exception as exc:
            self._emit(f"Scanner error: {exc}", "error")
            log.error("LoRa scanner error: %s", exc)
        finally:
            self._cleanup_radio(lora)
            self._emit("Scanner stopped.", "dim")

    # ------------------------------------------------------------------
    # Balloon Tracker — APRS 433 + UKHAS 868 listener
    # ------------------------------------------------------------------

    def start_tracker(self) -> None:
        """Start balloon tracker cycling APRS 433 and UKHAS 868 frequencies."""
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self.mode = "tracker"
        self.packets_received = 0
        self._thread = threading.Thread(
            target=self._run_tracker, daemon=True,
        )
        self._thread.start()

    def _run_tracker(self) -> None:
        lora = self._init_radio()
        if not lora:
            self.running = False
            return
        try:
            # Tracker profiles: APRS 433 MHz + UKHAS 868 MHz
            profiles = [
                # (freq_hz, sf, cr, bw, label)
                (433_775_000, 12, 5, 125_000, "433.775 SF12 APRS"),
                (434_855_000,  9, 7, 125_000, "434.855 SF9 APRS"),
                (868_100_000,  8, 5, 125_000, "868.1 SF8 UKHAS"),
            ]
            labels = ", ".join(p[4] for p in profiles)
            self._emit(f"Balloon tracker: {labels}", "success")
            self._emit(
                "Listening for LoRa APRS + UKHAS payloads...", "dim",
            )

            while not self._stop_event.is_set():
                for freq, sf, cr, bw, label in profiles:
                    if self._stop_event.is_set():
                        break
                    lora.setFrequency(freq)
                    lora.setLoRaModulation(sf, bw, cr, False)
                    lora.request(lora.RX_SINGLE)
                    lora.wait(3)  # 3s per profile
                    if lora.available() > 0:
                        data = self._read_packet(lora)
                        rssi = lora.packetRssi()
                        snr = lora.snr()
                        self.packets_received += 1
                        self._parse_balloon(data, rssi, snr, label)
        except Exception as exc:
            self._emit(f"Tracker error: {exc}", "error")
            log.error("LoRa tracker error: %s", exc)
        finally:
            self._cleanup_radio(lora)
            self._emit("Tracker stopped.", "dim")

    def _parse_balloon(
        self, data: bytearray, rssi: int, snr: float, tag: str = "",
    ) -> None:
        """Parse balloon payload — tries APRS, then UKHAS CSV, then raw."""
        # Try LoRa APRS format first (3-byte prefix \x3c\xff\x01)
        if len(data) >= 3 and data[:3] == APRS_PREFIX:
            self._parse_aprs(data[3:], rssi, snr, tag)
            return

        try:
            text = data.decode("utf-8", errors="replace").strip()

            # Try APRS without prefix (some trackers omit it)
            if ">" in text and ("=" in text or "!" in text or "/" in text):
                self._parse_aprs(data, rssi, snr, tag)
                return

            # Try UKHAS CSV: $$CALL,ID,TIME,LAT,LON,ALT,...
            clean = text.lstrip("$").strip()
            parts = clean.split(",")
            if len(parts) >= 6:
                call, sid, tm = parts[0], parts[1], parts[2]
                lat, lon, alt = parts[3], parts[4], parts[5]
                self._emit(
                    f"UKHAS [{call}] #{sid} {tm} ({tag})",
                    "attack_active",
                )
                self._emit(
                    f"  Pos: {lat},{lon}  Alt:{alt}m  "
                    f"RSSI:{rssi}dBm SNR:{snr:.1f}dB",
                    "success",
                )
                if len(parts) > 6:
                    extra = ",".join(parts[6:])
                    self._emit(f"  Extra: {extra}", "dim")
            else:
                # Unknown format — show raw
                self._emit(
                    f"[{self.packets_received}] {tag} {len(data)}B "
                    f"RSSI:{rssi}dBm SNR:{snr:.1f}dB",
                    "success",
                )
                self._emit(f"  {text}", "dim")
        except Exception:
            self._emit(
                f"[{self.packets_received}] {data.hex()}", "dim",
            )

    def _parse_aprs(
        self, data: bytearray, rssi: int, snr: float, tag: str = "",
    ) -> None:
        """Parse LoRa APRS packet: CALL>DEST:=DDMM.MMN/DDDMM.MMEO .../A=AAAAAA

        Format from SQ2CPA/LoRa_APRS_Balloon:
          CALL-11>APLAIX:=DDMM.MMN/DDDMM.MMEO SSS/PPP/A=AAAAAA/comment
        Position: DDMM.MM = degrees + decimal minutes
        Altitude: /A=AAAAAA in feet (6 digits)
        Comment fields: P(pkt) S(sats) F(freq) O(power) N(flight) etc.
        """
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            self._emit(
                f"[{self.packets_received}] APRS raw: {data.hex()}", "dim",
            )
            return

        # Parse CALL>DEST:payload
        m = re.match(r"([^>]+)>([^:]+):(.*)", text, re.DOTALL)
        if not m:
            self._emit(
                f"[{self.packets_received}] APRS? {tag} "
                f"RSSI:{rssi}dBm SNR:{snr:.1f}dB",
                "success",
            )
            self._emit(f"  {text}", "dim")
            return

        callsign = m.group(1)
        dest = m.group(2)
        payload = m.group(3)

        self._emit(
            f"APRS [{callsign}] → {dest} ({tag})",
            "attack_active",
        )

        # Try to extract position: =DDMM.MMN/DDDMM.MMEO or !DDMM.MMN/DDDMM.MMEO
        pos = re.search(
            r"[=!](\d{4}\.\d{2}[NS])[/\\](\d{5}\.\d{2}[EW])",
            payload,
        )
        if pos:
            lat_str, lon_str = pos.group(1), pos.group(2)
            lat = self._aprs_to_decimal(lat_str)
            lon = self._aprs_to_decimal(lon_str)
            pos_text = f"  Pos: {lat:.5f},{lon:.5f}"
        else:
            pos_text = "  Pos: (compressed/unknown)"

        # Extract altitude /A=NNNNNN (feet)
        alt_m = re.search(r"/A=(-?\d+)", payload)
        if alt_m:
            alt_ft = int(alt_m.group(1))
            alt_meters = alt_ft * 0.3048
            pos_text += f"  Alt:{alt_meters:.0f}m ({alt_ft}ft)"

        pos_text += f"  RSSI:{rssi}dBm SNR:{snr:.1f}dB"
        self._emit(pos_text, "success")

        # Show comment/telemetry (after position data)
        comment = re.sub(
            r"[=!]\d{4}\.\d{2}[NS][/\\]\d{5}\.\d{2}[EW]\S*\s*",
            "", payload,
        ).strip()
        if comment:
            self._emit(f"  {comment}", "dim")

    @staticmethod
    def _aprs_to_decimal(coord: str) -> float:
        """Convert APRS DDMM.MMN or DDDMM.MME to decimal degrees."""
        hemisphere = coord[-1]
        numeric = coord[:-1]
        if hemisphere in ("N", "S"):
            degrees = float(numeric[:2])
            minutes = float(numeric[2:])
        else:  # E, W
            degrees = float(numeric[:3])
            minutes = float(numeric[3:])
        decimal = degrees + minutes / 60.0
        if hemisphere in ("S", "W"):
            decimal = -decimal
        return decimal

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _read_packet(self, lora) -> bytearray:
        """Read all available bytes from radio buffer."""
        data = bytearray()
        while lora.available() > 0:
            data.append(lora.read())
        return data

    def _handle_packet(self, lora, tag: str) -> None:
        """Read a packet, log it with hex + ASCII."""
        data = self._read_packet(lora)
        rssi = lora.packetRssi()
        snr = lora.snr()
        self.packets_received += 1

        self._emit(
            f"[{self.packets_received}] {tag} {len(data)}B "
            f"RSSI:{rssi}dBm SNR:{snr:.1f}dB",
            "success",
        )
        self._emit(f"  HEX: {data.hex()}", "dim")

        # Try ASCII decode
        try:
            text = data.decode("utf-8", errors="replace")
            printable = "".join(
                c if c.isprintable() or c == " " else "." for c in text
            )
            self._emit(f"  TXT: {printable}", "dim")
        except Exception:
            pass

    def _cleanup_radio(self, lora) -> None:
        """Release SPI and mark as not running."""
        try:
            if lora:
                lora.end()
        except Exception:
            pass
        self.running = False

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
