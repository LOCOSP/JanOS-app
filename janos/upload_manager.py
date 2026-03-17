"""Upload wardriving data to WiGLE and handshakes to WPA-sec."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .config import (
    WIGLE_API_URL,
    WIGLE_API_NAME,
    WIGLE_API_TOKEN,
    WPASEC_URL,
    WPASEC_DL_URL,
    WPASEC_KEY,
)

log = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    """Config value with env override."""
    return os.environ.get(name, default) or ""


def wigle_configured() -> bool:
    """Return True if WiGLE credentials are set."""
    return bool(_env("JANOS_WIGLE_NAME", WIGLE_API_NAME)
                and _env("JANOS_WIGLE_TOKEN", WIGLE_API_TOKEN))


def wpasec_configured() -> bool:
    """Return True if WPA-sec key is set."""
    return bool(_env("JANOS_WPASEC_KEY", WPASEC_KEY))


def upload_wigle(csv_path: Path) -> tuple[bool, str]:
    """Upload a WiGLE-format CSV file.

    Returns (success, message).
    """
    name = _env("JANOS_WIGLE_NAME", WIGLE_API_NAME)
    token = _env("JANOS_WIGLE_TOKEN", WIGLE_API_TOKEN)
    if not name or not token:
        return False, "WiGLE credentials not configured"
    if not csv_path.is_file():
        return False, f"File not found: {csv_path}"
    try:
        import requests
    except ImportError:
        return False, "requests library not installed"
    try:
        with open(csv_path, "rb") as fh:
            files = {"file": (csv_path.name, fh, "text/csv")}
            resp = requests.post(
                WIGLE_API_URL,
                files=files,
                auth=(name, token),
                timeout=60,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return True, f"Uploaded! Observer: {data.get('observer', '?')}"
            return False, data.get("message", "Upload failed")
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        log.error("WiGLE upload error: %s", exc)
        return False, str(exc)


def upload_wpasec(pcap_path: Path) -> tuple[bool, str]:
    """Upload a .pcap file to WPA-sec.

    Returns (success, message).
    """
    key = _env("JANOS_WPASEC_KEY", WPASEC_KEY)
    if not key:
        return False, "WPA-sec key not configured"
    if not pcap_path.is_file():
        return False, f"File not found: {pcap_path}"
    try:
        import requests
    except ImportError:
        return False, "requests library not installed"
    try:
        with open(pcap_path, "rb") as fh:
            files = {"file": (pcap_path.name, fh, "application/octet-stream")}
            cookies = {"key": key}
            resp = requests.post(
                WPASEC_URL,
                files=files,
                cookies=cookies,
                timeout=60,
            )
        if resp.status_code == 200:
            body = resp.text.strip()
            if "already" in body.lower():
                return True, "Already submitted"
            return True, body[:200] if body else "Uploaded"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        log.error("WPA-sec upload error: %s", exc)
        return False, str(exc)


def upload_wpasec_all(loot_dir: Path) -> tuple[int, int, str]:
    """Upload all .pcap files from loot sessions to WPA-sec.

    Returns (uploaded_count, total_count, message).
    """
    pcaps = list(loot_dir.rglob("handshakes/*.pcap"))
    if not pcaps:
        return 0, 0, "No .pcap files found"
    uploaded = 0
    errors = []
    for p in pcaps:
        ok, msg = upload_wpasec(p)
        if ok:
            uploaded += 1
        else:
            errors.append(f"{p.name}: {msg}")
    total = len(pcaps)
    if errors:
        return uploaded, total, f"{uploaded}/{total} uploaded. Errors: {'; '.join(errors[:3])}"
    return uploaded, total, f"{uploaded}/{total} uploaded successfully"


def fetch_wigle_user_stats() -> Optional[dict]:
    """Fetch WiGLE user stats (discovered networks, rank, etc.).

    Returns dict with keys: discoveredWiFiGPS, discoveredBtGPS, rank, etc.
    Returns None on error or if not configured.
    """
    name = _env("JANOS_WIGLE_NAME", WIGLE_API_NAME)
    token = _env("JANOS_WIGLE_TOKEN", WIGLE_API_TOKEN)
    if not name or not token:
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.get(
            "https://api.wigle.net/api/v2/stats/user",
            auth=(name, token),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                stats = data.get("statistics", data)
                return stats
        return None
    except Exception as exc:
        log.error("WiGLE stats error: %s", exc)
        return None


def parse_potfile(potfile_path: Path) -> dict:
    """Parse WPA-sec potfile into structured dict.

    Format per line: AP_MAC:CLIENT_MAC:SSID:PASSWORD
    SSID may contain colons, so we split carefully.

    Returns {"by_ssid": {"SSID": [{"ap_mac": ..., "client_mac": ..., "password": ...}]}, "count": N}
    """
    result: dict = {"by_ssid": {}, "count": 0}
    if not potfile_path.is_file():
        return result
    try:
        text = potfile_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: AP_MAC:CLIENT_MAC:SSID:PASSWORD
        # AP_MAC and CLIENT_MAC are 17 chars each (xx:xx:xx:xx:xx:xx)
        # Split: first 2 fields are MACs, then SSID:PASSWORD
        parts = line.split(":", 2)  # -> [ap_mac_part, ...]
        # MAC addresses have internal colons, so we need char-level parsing
        # Reliable approach: first 17 chars = AP_MAC, char 18 = ':', next 17 = CLIENT_MAC, char 36 = ':', rest = SSID:PASSWORD
        if len(line) < 38:  # minimum: 17+1+17+1+1+1 = 38
            continue
        ap_mac = line[:17]
        if line[17] != ":":
            continue
        client_mac = line[18:35]
        if line[35] != ":":
            continue
        rest = line[36:]  # SSID:PASSWORD
        # Split from right — password is last field (password won't contain ':' normally)
        if ":" not in rest:
            continue
        ssid, password = rest.rsplit(":", 1)
        if not ssid:
            continue
        result["count"] += 1
        entry = {"ap_mac": ap_mac, "client_mac": client_mac, "password": password}
        if ssid not in result["by_ssid"]:
            result["by_ssid"][ssid] = []
        result["by_ssid"][ssid].append(entry)
    return result


def load_wpasec_passwords(loot_dir: Path) -> dict:
    """Load parsed WPA-sec passwords from JSON cache.

    Returns {"by_ssid": {...}, "count": N} or empty dict.
    Falls back to parsing potfile if JSON doesn't exist.
    """
    json_path = loot_dir / "passwords" / "wpasec_cracked.json"
    if json_path.is_file():
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "by_ssid" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: parse potfile directly
    potfile = loot_dir / "passwords" / "wpasec_cracked.potfile"
    if potfile.is_file():
        data = parse_potfile(potfile)
        # Save JSON cache
        _save_potfile_json(loot_dir, data)
        return data
    return {"by_ssid": {}, "count": 0}


def _save_potfile_json(loot_dir: Path, data: dict) -> None:
    """Save parsed potfile data as JSON cache."""
    try:
        pwd_dir = loot_dir / "passwords"
        pwd_dir.mkdir(parents=True, exist_ok=True)
        json_path = pwd_dir / "wpasec_cracked.json"
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError as exc:
        log.error("Cannot save potfile JSON: %s", exc)


def download_wpasec_passwords(loot_dir: Path) -> tuple[bool, int, str]:
    """Download cracked passwords from WPA-sec.

    Returns (success, count, message).
    Saves to loot_dir/passwords/wpasec_cracked.potfile
    Format per line: AP_MAC:CLIENT_MAC:SSID:PASSWORD
    """
    key = _env("JANOS_WPASEC_KEY", WPASEC_KEY)
    if not key:
        return False, 0, "WPA-sec key not configured"
    try:
        import requests
    except ImportError:
        return False, 0, "requests library not installed"
    try:
        resp = requests.get(
            WPASEC_DL_URL,
            cookies={"key": key},
            timeout=30,
        )
        if resp.status_code != 200:
            return False, 0, f"HTTP {resp.status_code}: {resp.text[:200]}"
        body = resp.text.strip()
        if not body:
            return True, 0, "No cracked passwords yet"
        lines = [ln for ln in body.splitlines() if ln.strip()]
        pwd_dir = loot_dir / "passwords"
        pwd_dir.mkdir(parents=True, exist_ok=True)
        out = pwd_dir / "wpasec_cracked.potfile"
        out.write_text(body + "\n", encoding="utf-8")
        # Parse and save JSON for fast SSID lookup
        parsed = parse_potfile(out)
        _save_potfile_json(loot_dir, parsed)
        return True, len(lines), f"{len(lines)} passwords saved"
    except Exception as exc:
        log.error("WPA-sec download error: %s", exc)
        return False, 0, str(exc)


def find_wardriving_csvs(loot_dir: Path) -> list[Path]:
    """Find all wardriving.csv files across loot sessions."""
    return sorted(loot_dir.rglob("wardriving.csv"))
