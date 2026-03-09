"""Sidebar mascot — animated ASCII creature that reacts to active operations."""

import random
import time

# ---------------------------------------------------------------------------
# Idle messages — randomly picked each tick for variety
# ---------------------------------------------------------------------------

_IDLE_MESSAGES = [
    "zzZ",
    "scan something!",
    "let's hack!",
    "*yawn*",
    "flash me?",
    "bored...",
    "sniff sniff?",
    "try portal!",
    "i see networks",
    "pwn time?",
    "pick a target",
    "feed me data",
    "...",
    "ready!",
    "*stretches*",
    "evil twin me!",
]

_last_idle_msg = ""

# ---------------------------------------------------------------------------
# Frame definitions: state -> list of (text, urwid_attr) tuples
# Each text is 4 lines joined by \n.  Cycled every 1-second tick.
# ---------------------------------------------------------------------------

_FRAMES = {
    # ── Idle: handled dynamically by _get_idle_frame() ───────────────────
    "idle": [],

    # ── Scan: looking around, searching ──────────────────────────────────
    "scan": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 o_o \u2502?\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 o_O \u2502 ??\n"
            "  \u2514\u2524 ~ \u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 O_o \u2502  ?\n"
            "  \u2514\u2524~  \u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
    ],

    # ── Sniffer: sniffing packets ────────────────────────────────────────
    "sniff": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u00b0_\u00b0 \u2502~ ~\n"
            "  \u2514\u2524  ~\u251c\u2518\n"
            "   \u2518   \u2514",
            "bold",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u00b0.\u00b0 \u2502 ~ ~\n"
            "  \u2514\u2524~  \u251c\u2518\n"
            "   \u2518   \u2514",
            "bold",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u00b0_\u00b0 \u2502  ~ ~\n"
            "  \u2514\u2524 ~ \u251c\u2518\n"
            "   \u2518   \u2514",
            "bold",
        ),
    ],

    # ── Deauth: zapping ──────────────────────────────────────────────────
    "deauth": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u00d7_\u00d7 \u2502 /\\/\\\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 >_< \u2502  \\/\\/\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
    ],

    # ── Blackout: lights out ─────────────────────────────────────────────
    "blackout": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 *_* \u2502 \u2591\u2591\u2591\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 o_o \u2502  \u2591\u2591\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
    ],

    # ── SAE overflow: flooding ───────────────────────────────────────────
    "sae_overflow": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 >_> \u2502 >>>\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 <_< \u2502  <<<\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
    ],

    # ── Handshake capture: catching ──────────────────────────────────────
    "handshake": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 @_@ \u2502  !!\n"
            "  \u2514\u2524\\o/\u251c\u2518\n"
            "   \u2518   \u2514",
            "success",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 @_@ \u2502 gotcha!\n"
            "  \u2514\u2524 Y \u251c\u2518\n"
            "   \u2518   \u2514",
            "success",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 @u@ \u2502  HS!\n"
            "  \u2514\u2524\\o/\u251c\u2518\n"
            "   \u2518   \u2514",
            "success",
        ),
    ],

    # ── Portal: fishing for creds ────────────────────────────────────────
    "portal": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 ^_^ \u2502~~o\n"
            "  \u2514\u2524  /\u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 ^.^ \u2502~o~\n"
            "  \u2514\u2524  /\u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 ^_^ \u2502o!!\n"
            "  \u2514\u2524  /\u251c\u2518\n"
            "   \u2518   \u2514",
            "warning",
        ),
    ],

    # ── Evil Twin: the clone ─────────────────────────────────────────────
    "evil_twin": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u2022_\u2022 \u2502\u2502 \u2022_\u2022 \u2502\n"
            "  \u2514\u2524   \u251c\u2518\u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514  \u2518   \u2514",
            "attack_active",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510 \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u2022_\u2022 \u2502 \u2502 \u2022_\u2022 \u2502\n"
            "  \u2514\u2524   \u251c\u2518 \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514   \u2518   \u2514",
            "attack_active",
        ),
    ],

    # ── Flash: working with tools ────────────────────────────────────────
    "flash": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 >_< \u2502 ..\n"
            "  \u2514\u2524/| \u251c\u2518\n"
            "   \u2518   \u2514",
            "attack_active",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 x_x \u2502 //\n"
            "  \u2514\u2524|/ \u251c\u2518\n"
            "   \u2518   \u2514",
            "attack_active",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 >_< \u2502' '\n"
            "  \u2514\u2524/| \u251c\u2518\n"
            "   \u2518   \u2514",
            "attack_active",
        ),
    ],

    # ── AIO toggle: brief celebration ────────────────────────────────────
    "aio_toggle": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 ^_^ \u2502 +!\n"
            "  \u2514\u2524\\o/\u251c\u2518\n"
            "   \u2518   \u2514",
            "success",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 ^.^ \u2502  *\n"
            "  \u2514\u2524 Y \u251c\u2518\n"
            "   \u2518   \u2514",
            "success",
        ),
    ],

    # ── Crash: firmware panic ────────────────────────────────────────────
    "crash": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 X_X \u2502 !!\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 @_@ \u2502!!!\n"
            "  \u2514\u2524/|\\\u251c\u2518\n"
            "   \u2518   \u2514",
            "error",
        ),
    ],
}


# ---------------------------------------------------------------------------
# State detection + frame selection
# ---------------------------------------------------------------------------

def get_creature_state(state) -> str:
    """Pick the highest-priority animation state from AppState flags."""
    if state.firmware_crashed:
        return "crash"
    if state.evil_twin_running:
        return "evil_twin"
    if state.portal_running:
        return "portal"
    if state.handshake_running:
        return "handshake"
    if state.blackout_running:
        return "blackout"
    if state.sae_overflow_running:
        return "sae_overflow"
    if state.attack_running:
        return "deauth"
    if state.flashing:
        return "flash"
    if state.scanning:
        return "scan"
    if state.sniffer_running:
        return "sniff"
    if state.aio_toggling and (time.time() - state.aio_toggling) < 3.0:
        return "aio_toggle"
    return "idle"


def _get_idle_frame(tick: int):
    """Generate an idle frame with a random funny message."""
    global _last_idle_msg

    msg = random.choice(_IDLE_MESSAGES)
    while msg == _last_idle_msg and len(_IDLE_MESSAGES) > 1:
        msg = random.choice(_IDLE_MESSAGES)
    _last_idle_msg = msg

    # Blink cycle: open → closed → open+msg
    if tick % 3 == 1:
        face = "-_-"
    else:
        face = "\u2022_\u2022"

    text = (
        "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
        f"  \u2502 {face} \u2502  {msg}\n"
        "  \u2514\u2524   \u251c\u2518\n"
        "   \u2518   \u2514"
    )
    return text, "dim"


def get_frame(state_name: str, tick: int):
    """Return (text, urwid_attr) for the current animation frame."""
    if state_name == "idle":
        return _get_idle_frame(tick)

    frames = _FRAMES.get(state_name)
    if not frames:
        return _get_idle_frame(tick)
    text, attr = frames[tick % len(frames)]
    return text, attr
