"""Auto-update checker — compares local version with GitHub remote.

Non-blocking: runs in a daemon thread so it never delays startup.
Falls back silently on any network error (no internet = no dialog).
Also checks firmware release version on GitHub.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from .config import APP_UPDATE_URL, FIRMWARE_RELEASE_URL

log = logging.getLogger(__name__)


def check_remote_version(timeout: int = 5) -> str | None:
    """Fetch ``__version__`` from the remote ``__init__.py`` on GitHub.

    Returns the version string (e.g. ``"2.3.0"``) or *None* on any error.
    """
    try:
        req = Request(APP_UPDATE_URL)
        req.add_header("User-Agent", "JanOS-App")
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)
    except Exception as exc:
        log.debug("Update check failed: %s", exc)
    return None


def is_newer(remote: str, local: str) -> bool:
    """Return *True* if *remote* version is strictly newer than *local*.

    Compares as integer tuples so ``"2.3.0" > "2.2.0"`` works correctly.
    Non-numeric parts are silently ignored.
    """
    def _to_tuple(v: str) -> tuple[int, ...]:
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        return tuple(parts)

    return _to_tuple(remote) > _to_tuple(local)


def do_git_pull(
    app_dir: str,
    callback: Callable[[str, str], None],
) -> bool:
    """Run ``git pull`` in *app_dir* and install updated deps.

    After a successful pull, runs ``pip install -r requirements.txt``
    inside the project ``.venv/`` (if it exists) to pick up any new
    or changed dependencies.

    *callback(line, attr)* is called for each output line.
    Returns *True* on success.
    """
    # Resolve project root (git repo root, where .venv/ lives)
    project_root = _find_project_root(app_dir)

    callback("Updating from GitHub...", "attack_active")
    try:
        # Ensure 'github' remote exists (public, no auth needed)
        _ensure_github_remote(project_root)

        # Stash any local changes first (e.g. manually edited files)
        subprocess.run(
            ["git", "stash", "--quiet"],
            cwd=project_root,
            capture_output=True,
            timeout=10,
        )
        proc = subprocess.Popen(
            ["git", "pull", "github", "main"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                callback(f"  {line}", "dim")
        proc.wait()
        if proc.returncode != 0:
            callback(f"git pull failed (exit code {proc.returncode})", "error")
            return False

        # Install/update dependencies in .venv
        _pip_install(project_root, callback)

        callback("Update complete! Restart the app.", "success")
        return True
    except FileNotFoundError:
        callback("git not found — install git or update manually.", "error")
        return False
    except Exception as exc:
        callback(f"Update error: {exc}", "error")
        return False


def _find_project_root(start: str) -> str:
    """Walk up from *start* to find the directory containing ``.git/``."""
    current = os.path.abspath(start)
    for _ in range(10):  # safety limit
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(start)  # fallback


def _pip_install(
    project_root: str,
    callback: Callable[[str, str], None],
) -> None:
    """Install deps after update.

    Prefers ``setup.sh`` (handles pip install **and** Pi 5 rpi-lgpio fix).
    Falls back to plain ``pip install`` on Windows or when bash is missing.
    """
    # --- Try setup.sh first (Linux/macOS) ---
    setup_script = os.path.join(project_root, "setup.sh")
    if os.path.isfile(setup_script) and shutil.which("bash"):
        callback("Running setup.sh (deps + platform fixes)...", "dim")
        try:
            proc = subprocess.Popen(
                ["bash", setup_script],
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                if line:
                    callback(f"  {line}", "dim")
            proc.wait()
            if proc.returncode == 0:
                callback("Dependencies up to date.", "dim")
            else:
                callback("setup.sh had warnings (non-fatal).", "warning")
            return
        except Exception as exc:
            callback(
                f"setup.sh error: {exc} — falling back to pip...", "warning",
            )

    # --- Fallback: plain pip install (Windows / no bash) ---
    req_file = os.path.join(project_root, "requirements.txt")
    if not os.path.isfile(req_file):
        return

    venv_dir = os.path.join(project_root, ".venv")
    venv_pip = os.path.join(venv_dir, "bin", "pip")
    if os.path.isfile(venv_pip):
        pip_cmd = [venv_pip]
    else:
        pip_cmd = [sys.executable, "-m", "pip"]

    callback("Installing updated dependencies...", "dim")
    try:
        proc = subprocess.Popen(
            [*pip_cmd, "install", "-q", "-r", req_file],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                callback(f"  {line}", "dim")
        proc.wait()
        if proc.returncode == 0:
            callback("Dependencies up to date.", "dim")
        else:
            callback("pip install had warnings (non-fatal).", "warning")
    except Exception as exc:
        callback(f"pip install error: {exc} (non-fatal)", "warning")


GITHUB_REPO_URL = "https://github.com/LOCOSP/JanOS-app.git"


def _ensure_github_remote(app_dir: str) -> None:
    """Make sure a ``github`` remote exists pointing to the public repo."""
    result = subprocess.run(
        ["git", "remote", "get-url", "github"],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Remote doesn't exist — add it
        subprocess.run(
            ["git", "remote", "add", "github", GITHUB_REPO_URL],
            cwd=app_dir,
            capture_output=True,
        )


# ------------------------------------------------------------------ #
# Firmware version helpers
# ------------------------------------------------------------------ #

def _real_home() -> Path:
    """Return the real user's home even when running under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(os.path.expanduser(f"~{sudo_user}"))
    return Path.home()

_FW_VERSION_FILE = _real_home() / ".janos_fw_version"


def check_remote_firmware_version(timeout: int = 10) -> str | None:
    """Fetch the latest firmware release tag from GitHub.

    Returns the tag name (e.g. ``"v1.5.5"``) or *None* on any error.
    """
    try:
        req = Request(FIRMWARE_RELEASE_URL)
        req.add_header("User-Agent", "JanOS-App")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("tag_name")
    except Exception as exc:
        log.debug("Firmware version check failed: %s", exc)
    return None


def get_local_fw_version() -> str | None:
    """Read the last-flashed firmware version from ``~/.janos_fw_version``."""
    try:
        if _FW_VERSION_FILE.exists():
            ver = _FW_VERSION_FILE.read_text(encoding="utf-8").strip()
            return ver or None
    except Exception:
        pass
    return None


def save_local_fw_version(version: str) -> None:
    """Persist firmware version after a successful flash."""
    try:
        _FW_VERSION_FILE.write_text(version.strip(), encoding="utf-8")
        log.info("Saved firmware version: %s", version.strip())
    except Exception as exc:
        log.warning("Cannot save firmware version: %s", exc)
