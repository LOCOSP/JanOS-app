"""Upload wardriving data to WiGLE and handshakes to WPA-sec."""

import logging
import os
from pathlib import Path
from typing import Optional

from .config import (
    WIGLE_API_URL,
    WIGLE_API_NAME,
    WIGLE_API_TOKEN,
    WPASEC_URL,
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


def find_wardriving_csvs(loot_dir: Path) -> list[Path]:
    """Find all wardriving.csv files across loot sessions."""
    return sorted(loot_dir.rglob("wardriving.csv"))
