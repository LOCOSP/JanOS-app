"""16-color urwid palette — safe for serial terminals and SSH."""

PALETTE = [
    # (name, foreground, background)
    # General
    ("default",       "white",        "black"),
    ("bold",          "white,bold",   "black"),
    ("dim",           "dark gray",    "black"),

    # Header / banner
    ("banner",        "light cyan",   "black"),
    ("header",        "white,bold",   "dark blue"),
    ("header_device", "yellow",       "dark blue"),

    # Tab bar
    ("tab_active",    "white,bold",   "dark cyan"),
    ("tab_inactive",  "light gray",   "dark gray"),

    # Footer / status bar
    ("footer",        "white",        "dark blue"),
    ("footer_key",    "yellow,bold",  "dark blue"),
    ("footer_alert",  "light red,bold", "dark blue"),

    # Tables
    ("table_header",  "white,bold",   "dark gray"),
    ("table_row",     "white",        "black"),
    ("table_row_sel", "white,bold",   "dark cyan"),

    # RSSI colors
    ("rssi_good",     "light green",  "black"),
    ("rssi_fair",     "yellow",       "black"),
    ("rssi_weak",     "light red",    "black"),

    # Attack status
    ("attack_active", "light red,bold", "black"),
    ("attack_idle",   "dark gray",    "black"),

    # Sniffer
    ("sniffer_live",  "light cyan",   "black"),
    ("sniffer_count", "light green",  "black"),

    # Portal / Evil Twin
    ("portal",        "light blue",   "black"),
    ("evil_twin",     "light magenta", "black"),

    # Dialogs
    ("dialog",        "white",        "dark gray"),
    ("dialog_title",  "white,bold",   "dark gray"),
    ("dialog_btn",    "white,bold",   "dark cyan"),
    ("dialog_btn_f",  "white,bold",   "dark red"),

    # Crash / error overlay
    ("crash",         "white,bold",   "dark red"),

    # Misc
    ("success",       "light green",  "black"),
    ("warning",       "yellow",       "black"),
    ("error",         "light red",    "black"),
]
