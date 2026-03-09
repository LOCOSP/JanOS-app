"""Sidebar mascot — animated ASCII creature that reacts to active operations."""

# ---------------------------------------------------------------------------
# Frame definitions: state → list of (text, urwid_attr) tuples
# Each text is 4 lines joined by \n.  Cycled every 1-second tick.
# ---------------------------------------------------------------------------

_FRAMES = {
    # ── Idle: blink, sleep ────────────────────────────────────────────────
    "idle": [
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u2022_\u2022 \u2502\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "dim",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 -_- \u2502\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "dim",
        ),
        (
            "  \u250c\u2500\u2500\u2500\u2500\u2500\u2510\n"
            "  \u2502 \u2022_\u2022 \u2502  zzZ\n"
            "  \u2514\u2524   \u251c\u2518\n"
            "   \u2518   \u2514",
            "dim",
        ),
    ],

    # ── Sniffer: sniffing packets ─────────────────────────────────────────
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

    # ── Deauth: zapping ───────────────────────────────────────────────────
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

    # ── Blackout: lights out ──────────────────────────────────────────────
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

    # ── SAE overflow: flooding ────────────────────────────────────────────
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

    # ── Handshake capture: catching ───────────────────────────────────────
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

    # ── Portal: fishing for creds ─────────────────────────────────────────
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

    # ── Evil Twin: the clone ──────────────────────────────────────────────
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
}


def get_creature_state(state) -> str:
    """Pick the highest-priority animation state from AppState flags."""
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
    if state.sniffer_running:
        return "sniff"
    return "idle"


def get_frame(state_name: str, tick: int):
    """Return (text, urwid_attr) for the current animation frame."""
    frames = _FRAMES.get(state_name, _FRAMES["idle"])
    text, attr = frames[tick % len(frames)]
    return text, attr
