"""Privacy mode — mask sensitive data on screen for content creation.

When private mode is active, all SSIDs, MAC addresses, IP addresses,
passwords and other sensitive data are redacted in the display layer.
Loot files are NOT affected — they always contain full data.

Usage:
    from janos.privacy import mask_ssid, mask_mac, mask_line, is_private
    display_text = mask_ssid(ssid)      # "eduroam" → "ed****"
    display_mac  = mask_mac(mac)        # "C4:EE:6E:5D:01:AB" → "C4:EE:**:**:**:**"
    display_line = mask_line(raw_line)   # masks MACs, IPs, passwords in free text
"""

import re

_private_mode: bool = False


def set_private_mode(enabled: bool) -> None:
    global _private_mode
    _private_mode = enabled


def is_private() -> bool:
    return _private_mode


# ------------------------------------------------------------------
# Individual masking functions
# ------------------------------------------------------------------

def mask_ssid(ssid: str) -> str:
    """Mask SSID: show first 2 chars, replace rest with asterisks.

    Examples:
        "eduroam"     → "ed*****"
        "MyWiFi"      → "My****"
        "AB"          → "**"
        ""            → ""
    """
    if not _private_mode or not ssid:
        return ssid
    if len(ssid) <= 2:
        return "*" * len(ssid)
    return ssid[:2] + "*" * (len(ssid) - 2)


def mask_mac(mac: str) -> str:
    """Mask MAC address: keep first 2 octets (vendor prefix), mask rest.

    Examples:
        "C4:EE:6E:5D:01:AB" → "C4:EE:**:**:**:**"
        "0a:f1:e6:6e:5d:01" → "0a:f1:**:**:**:**"
    """
    if not _private_mode or not mac:
        return mac
    parts = mac.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:2] + ["**"] * (len(parts) - 2))
    return mac


def mask_ip(ip: str) -> str:
    """Mask IP address: keep first octet, mask rest.

    Examples:
        "192.168.1.100" → "192.*.*.*"
        "10.59.40.57"   → "10.*.*.*"
    """
    if not _private_mode or not ip:
        return ip
    parts = ip.split(".")
    if len(parts) == 4:
        return parts[0] + ".*.*.*"
    return ip


def mask_password(pw: str) -> str:
    """Fully mask a password string."""
    if not _private_mode or not pw:
        return pw
    return "*" * min(8, max(len(pw), 4))


# ------------------------------------------------------------------
# Line-level masking (for serial output / log lines)
# ------------------------------------------------------------------

# Regex patterns
_MAC_RE = re.compile(
    r'([0-9a-fA-F]{2}:[0-9a-fA-F]{2})'
    r':([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})'
)

_IP_RE = re.compile(
    r'(\d{1,3})\.\d{1,3}\.\d{1,3}\.\d{1,3}'
)

_PASSWORD_RE = re.compile(
    r'((?:Password|password|PASS|pass|pwd|PWD)\s*[:=]\s*)(.*)',
    re.IGNORECASE,
)

_SSID_RE = re.compile(
    r'((?:SSID|ssid)\s*[:=]\s*)(\S+)',
    re.IGNORECASE,
)


def mask_line(line: str) -> str:
    """Apply all masking rules to a free-form text line.

    Masks MAC addresses, IP addresses, passwords, and SSID references.
    """
    if not _private_mode or not line:
        return line

    # Mask MAC addresses (keep first 2 octets)
    line = _MAC_RE.sub(r'\1:**:**:**:**', line)

    # Mask IP addresses (keep first octet)
    line = _IP_RE.sub(r'\1.*.*.*', line)

    # Mask passwords
    line = _PASSWORD_RE.sub(lambda m: m.group(1) + "********", line)

    # Mask SSIDs in "SSID: xxx" or "SSID=xxx" patterns
    def _mask_ssid_match(m):
        prefix = m.group(1)
        ssid_val = m.group(2)
        if len(ssid_val) <= 2:
            return prefix + "*" * len(ssid_val)
        return prefix + ssid_val[:2] + "*" * (len(ssid_val) - 2)

    line = _SSID_RE.sub(_mask_ssid_match, line)

    return line
