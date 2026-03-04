"""Cyberpunk neon urwid palette — 16-color safe for serial terminals."""

PALETTE = [
    # (name, foreground, background)
    # General
    ("default",       "light cyan",   "black"),
    ("bold",          "light cyan,bold", "black"),
    ("dim",           "dark cyan",    "black"),

    # Header / banner
    ("banner",        "light magenta,bold", "black"),
    ("header",        "light cyan,bold", "dark magenta"),
    ("header_device", "white,bold",   "dark magenta"),

    # Tab bar
    ("tab_active",    "white,bold",   "dark magenta"),
    ("tab_inactive",  "dark cyan",    "black"),

    # Footer / status bar
    ("footer",        "light cyan",   "dark blue"),
    ("footer_key",    "light magenta,bold", "dark blue"),
    ("footer_alert",  "yellow,bold",  "dark blue"),

    # Tables
    ("table_header",  "light magenta,bold", "black"),
    ("table_row",     "light cyan",   "black"),
    ("table_row_sel", "white,bold",   "dark magenta"),

    # RSSI colors
    ("rssi_good",     "light green",  "black"),
    ("rssi_fair",     "yellow",       "black"),
    ("rssi_weak",     "light red",    "black"),

    # Attack status
    ("attack_active", "light red,bold", "black"),
    ("attack_idle",   "dark cyan",    "black"),

    # Sniffer
    ("sniffer_live",  "dark cyan",    "black"),
    ("sniffer_count", "light green,bold", "black"),

    # Portal / Evil Twin
    ("portal",        "light cyan",   "black"),
    ("evil_twin",     "light magenta", "black"),

    # Dialogs
    ("dialog",        "light cyan",   "dark blue"),
    ("dialog_title",  "light magenta,bold", "dark blue"),
    ("dialog_btn",    "white,bold",   "dark magenta"),
    ("dialog_btn_f",  "white,bold",   "dark red"),

    # Crash / error overlay
    ("crash",         "white,bold",   "dark red"),

    # Misc
    ("success",       "light green",  "black"),
    ("warning",       "yellow",       "black"),
    ("error",         "light red",    "black"),
]
