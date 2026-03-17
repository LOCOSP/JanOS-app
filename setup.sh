#!/bin/bash
# JanOS — setup virtual environment and install dependencies
set -e
cd "$(dirname "$0")"

echo "=== JanOS Setup ==="

if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv --system-site-packages .venv
else
    echo "[*] Virtual environment already exists."
fi

# Ensure system site-packages are accessible (needed for pybluez, dbus-python)
if grep -q 'include-system-site-packages = false' .venv/pyvenv.cfg 2>/dev/null; then
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
    echo "[*] Enabled system site-packages in venv"
fi

IS_PI5=false
if grep -qi "Raspberry Pi.*5\|Compute Module 5" /proc/device-tree/model 2>/dev/null; then
    IS_PI5=true
fi

# Pi 5: remove old symlinks BEFORE pip install (pip can't overwrite root-owned symlinks)
if $IS_PI5; then
    VENV_SP="$(.venv/bin/python3 -c 'import site; print(site.getsitepackages()[0])')"
    [ -L "$VENV_SP/lgpio.py" ] && rm -f "$VENV_SP/lgpio.py"
    rm -f "$VENV_SP"/_lgpio*.so 2>/dev/null || true
    [ -L "$VENV_SP/RPi" ] && rm -f "$VENV_SP/RPi"
fi

echo "[*] Installing system dependencies..."
for pkg in tcpdump aircrack-ng; do
    if ! command -v "$pkg" &>/dev/null; then
        echo "    Installing $pkg..."
        sudo apt-get install -y -qq "$pkg" 2>/dev/null || echo "    [!] Failed to install $pkg"
    fi
done

echo "[*] Installing Python dependencies..."
.venv/bin/pip install -q -r requirements.txt

# Raspberry Pi 5 (BCM2712): RPi.GPIO from PyPI doesn't work.
# Replace with system rpi-lgpio shim (uses lgpio/gpiochip).
if $IS_PI5; then
    SYS_SP="/usr/lib/python3/dist-packages"
    VENV_SP="$(.venv/bin/python3 -c 'import site; print(site.getsitepackages()[0])')"

    if [ -f "$SYS_SP/lgpio.py" ] && [ -d "$SYS_SP/RPi" ]; then
        echo "[*] Pi 5 detected — linking system rpi-lgpio into venv..."
        # Remove PyPI RPi.GPIO (incompatible with Pi 5)
        .venv/bin/pip uninstall -y RPi.GPIO 2>/dev/null || true
        rm -rf "$VENV_SP/RPi" "$VENV_SP/RPi.GPIO"* 2>/dev/null || true
        # Symlink system lgpio + rpi-lgpio shim
        ln -sf "$SYS_SP/lgpio.py" "$VENV_SP/lgpio.py"
        for f in "$SYS_SP"/_lgpio*.so; do
            [ -f "$f" ] && ln -sf "$f" "$VENV_SP/"
        done
        ln -sf "$SYS_SP/RPi" "$VENV_SP/RPi"
        echo "    Linked: lgpio + RPi.GPIO (rpi-lgpio shim)"
    else
        echo "[!] Pi 5 detected but python3-rpi-lgpio not installed."
        echo "    Run: sudo apt install python3-rpi-lgpio python3-lgpio"
    fi
fi

echo ""
echo "[OK] Setup complete!"
echo ""
echo "Run JanOS:"
echo "  ./run.sh                  # auto-detect ESP32"
echo "  ./run.sh /dev/ttyUSB0     # specify device"
echo ""
echo "Or manually:"
echo "  .venv/bin/python3 -m janos"
