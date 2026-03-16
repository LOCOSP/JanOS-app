"""Entry point: python3 -m janos /dev/ttyUSB0"""

import sys
import logging

from .tui.app import JanOSTUI


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if args:
        device = args[0]
    else:
        # Auto-detect ESP32 serial port
        from .serial_manager import detect_esp32_port
        device = detect_esp32_port() or ""

    # Logging setup
    if "--debug" in flags:
        logging.basicConfig(
            filename="/tmp/janos.log",
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    # Legacy mode fallback
    if "--legacy" in flags:
        import importlib
        import os
        # Try importing the old JanOS_app.py as module
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        old_script = os.path.join(app_dir, "JanOS_app.py")
        if os.path.exists(old_script):
            sys.argv = [old_script, device]
            spec = importlib.util.spec_from_file_location("JanOS_app", old_script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return
        else:
            print("Legacy JanOS_app.py not found. Starting TUI mode.")

    tui = JanOSTUI(device)
    tui.run()


if __name__ == "__main__":
    main()
