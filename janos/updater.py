"""Auto-update checker — compares local version with GitHub remote.

Non-blocking: runs in a daemon thread so it never delays startup.
Falls back silently on any network error (no internet = no dialog).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Callable
from urllib.request import Request, urlopen

from .config import APP_UPDATE_URL

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
    """Run ``git pull`` in *app_dir* and stream output to *callback*.

    *callback(line, attr)* is called for each output line.
    Returns *True* on success.
    """
    callback("Running git pull...", "attack_active")
    try:
        proc = subprocess.Popen(
            ["git", "pull"],
            cwd=app_dir,
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
            callback("Update complete! Restart the app.", "success")
            return True
        else:
            callback(f"git pull failed (exit code {proc.returncode})", "error")
            return False
    except FileNotFoundError:
        callback("git not found — install git or update manually.", "error")
        return False
    except Exception as exc:
        callback(f"Update error: {exc}", "error")
        return False
