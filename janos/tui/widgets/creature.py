"""Sidebar mascot — animated ASCII creature that reacts to active operations."""

import random
import time

# ---------------------------------------------------------------------------
# Idle messages — phased by how long the creature has been idle
# ---------------------------------------------------------------------------

# Phase 1: just came back to idle, sleepy (0–6 s)
_IDLE_QUIET = ["zzZ", "...", "*yawn*", "  ", "zzz.."]

# Phase 2: waking up, stretching (6–16 s)
_IDLE_WAKING = ["*stretches*", "ready!", "bored...", "hmm...", "sup?", "*blink*"]

# Phase 3: actively nudging the user toward features (16 s+)
_IDLE_NUDGE = [
    "scan something!",
    "let's hack!",
    "flash me?",
    "sniff sniff?",
    "try portal!",
    "i see networks",
    "pwn time?",
    "pick a target",
    "feed me data",
    "evil twin me!",
    "BLE hunt?",
    "find airtags!",
    "track someone?",
    "LoRa time!",
    "mesh me up!",
    "radio waves~",
    "wardrive?",
    "GPS ready!",
]

# Idle state tracking
_idle_ticks: int = 0        # consecutive ticks spent in idle
_idle_current_msg: str = ""  # message currently displayed
_idle_msg_age: int = 0       # how many ticks current message has been shown

_MSG_HOLD = 5     # hold each message for 5 ticks (≈ 5 s)
_QUIET_UNTIL = 6  # phase 1 ends after 6 s
_WAKING_UNTIL = 16  # phase 2 ends after 16 s

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

    # ── BLE Scan: antenna sweeping ─────────────────────────────────────
    "bt_scan": [
        (
            "  ┌─────┐\n"
            "  │ °_° │ )))  \n"
            "  └┤ | ├┘\n"
            "   ┘   └",
            "bold",
        ),
        (
            "  ┌─────┐\n"
            "  │ °.° │  ))) \n"
            "  └┤ | ├┘\n"
            "   ┘   └",
            "bold",
        ),
        (
            "  ┌─────┐\n"
            "  │ °_° │   )))\n"
            "  └┤ | ├┘\n"
            "   ┘   └",
            "bold",
        ),
    ],

    # ── BLE Tracker: following signal ──────────────────────────────────
    "bt_tracking": [
        (
            "  ┌─────┐\n"
            "  │ >_> │ ...\n"
            "  └┤  >├┘\n"
            "   ┘   └",
            "warning",
        ),
        (
            "  ┌─────┐\n"
            "  │ >_> │ .!.\n"
            "  └┤ >>├┘\n"
            "   ┘   └",
            "warning",
        ),
        (
            "  ┌─────┐\n"
            "  │ >_> │ !.!\n"
            "  └┤>>>├┘\n"
            "   ┘   └",
            "warning",
        ),
    ],

    # ── AirTag Scanner: detecting tags ─────────────────────────────────
    "bt_airtag": [
        (
            "  ┌─────┐\n"
            "  │ o_o │ [*]\n"
            "  └┤   ├┘\n"
            "   ┘   └",
            "attack_active",
        ),
        (
            "  ┌─────┐\n"
            "  │ O_O │[*!]\n"
            "  └┤   ├┘\n"
            "   ┘   └",
            "attack_active",
        ),
        (
            "  ┌─────┐\n"
            "  │ o_O │ [*]\n"
            "  └┤ ! ├┘\n"
            "   ┘   └",
            "attack_active",
        ),
    ],

    # ── Wardriving: GPS + WiFi scan ──────────────────────────────────
    "wardriving": [
        (
            "  ┌─────┐\n"
            "  │ °_° │>*>\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
        ),
        (
            "  ┌─────┐\n"
            "  │ °.° │ >*>\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
        ),
        (
            "  ┌─────┐\n"
            "  │ °_° │  >*>\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
        ),
    ],

    # ── Dragon Drain: fire/flood ─────────────────────────────────────
    "dragon_drain": [
        (
            "  ┌─────┐\n"
            "  │ >_< │≋⚡≋\n"
            "  └┤/|\\├┘\n"
            "   ┘   └",
            "error",
        ),
        (
            "  ┌─────┐\n"
            "  │ x_x │ ≋⚡\n"
            "  └┤\\|/├┘\n"
            "   ┘   └",
            "error",
        ),
        (
            "  ┌─────┐\n"
            "  │ >_> │⚡≋⚡\n"
            "  └┤/|\\├┘\n"
            "   ┘   └",
            "error",
        ),
    ],

    # ── MITM: intercepting traffic ───────────────────────────────────
    "mitm": [
        (
            "  ┌─────┐\n"
            "  │ •_• │←·→\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "attack_active",
        ),
        (
            "  ┌─────┐\n"
            "  │ •.• │·←→·\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "attack_active",
        ),
        (
            "  ┌─────┐\n"
            "  │ •_• │←··→\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "attack_active",
        ),
    ],

    # ── LoRa: radio waves ──────────────────────────────────────────────
    "lora": [
        (
            "  ┌─────┐\n"
            "  │ °_° │ ~=~\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
        ),
        (
            "  ┌─────┐\n"
            "  │ °.° │=~=~\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
        ),
        (
            "  ┌─────┐\n"
            "  │ °_° │~=~=\n"
            "  └┤/Y\\├┘\n"
            "   ┘   └",
            "success",
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
    if state.dragon_drain_running:
        return "dragon_drain"
    if state.mitm_running:
        return "mitm"
    if state.attack_running:
        return "deauth"
    if state.flashing:
        return "flash"
    if state.bt_tracking_running:
        return "bt_tracking"
    if state.bt_airtag_running:
        return "bt_airtag"
    if state.bt_wardriving_running:
        return "bt_scan"
    if state.bt_scan_running:
        return "bt_scan"
    if state.lora_running:
        return "lora"
    if state.wardriving_running:
        return "wardriving"
    if state.scanning:
        return "scan"
    if state.sniffer_running:
        return "sniff"
    if state.aio_toggling and (time.time() - state.aio_toggling) < 3.0:
        return "aio_toggle"
    return "idle"


def _pick_from(pool: list) -> str:
    """Pick a random message from *pool*, avoiding the last one shown."""
    global _idle_current_msg
    msg = random.choice(pool)
    while msg == _idle_current_msg and len(pool) > 1:
        msg = random.choice(pool)
    _idle_current_msg = msg
    return msg


def _get_idle_frame(tick: int):
    """Generate an idle frame with phased, slow-changing messages."""
    global _idle_ticks, _idle_current_msg, _idle_msg_age

    _idle_ticks += 1
    _idle_msg_age += 1

    # Pick a new message when the hold time expires (or first frame)
    if _idle_msg_age >= _MSG_HOLD or not _idle_current_msg:
        _idle_msg_age = 0
        if _idle_ticks < _QUIET_UNTIL:
            _pick_from(_IDLE_QUIET)
        elif _idle_ticks < _WAKING_UNTIL:
            _pick_from(_IDLE_WAKING)
        else:
            _pick_from(_IDLE_NUDGE)

    msg = _idle_current_msg

    # Blink cycle: open → closed → open
    if tick % 4 == 2:
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
    global _idle_ticks, _idle_msg_age, _idle_current_msg

    if state_name != "idle":
        # Reset idle tracking when any operation is active
        _idle_ticks = 0
        _idle_msg_age = 0
        _idle_current_msg = ""

    if state_name == "idle":
        return _get_idle_frame(tick)

    frames = _FRAMES.get(state_name)
    if not frames:
        return _get_idle_frame(tick)
    text, attr = frames[tick % len(frames)]
    return text, attr
